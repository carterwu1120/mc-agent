"""Exercise the real event router with fake transport/LLM, without starting MC."""
import asyncio
import importlib
import json
from unittest.mock import AsyncMock, Mock

import pytest

from agent.recovery import RecoverySupervisor
from agent.executor import PlanExecutor


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr('agent.logger.init_logger', lambda *_: None)
    monkeypatch.setattr('agent.brain.build_client', lambda: None)
    app = importlib.import_module('agent.agent')
    monkeypatch.setattr(app, 'recovery', RecoverySupervisor(decision_timeout=0.03))
    monkeypatch.setattr(app, 'executor', PlanExecutor())
    monkeypatch.setattr(app, '_thinking', set())
    monkeypatch.setattr(app, '_latest_state', {'activity': 'idle'})
    task = {'id': 'original', 'goal': '取得鐵', 'status': 'running', 'currentStep': 0,
            'steps': [{'cmd': 'mine iron 3', 'status': 'running'}, {'cmd': 'smelt raw_iron 3', 'status': 'pending'}]}
    monkeypatch.setattr(app.task_memory, 'load', lambda: task)
    monkeypatch.setattr(app.task_memory, 'interrupt', lambda reason: task.update(status='interrupted', interruptedBy=reason))
    monkeypatch.setattr(app.task_memory, 'record_event', lambda *a, **k: None)
    def update_runtime(patch):
        runtime = dict(task.get('runtime') or {})
        runtime.update(patch)
        task['runtime'] = runtime

    monkeypatch.setattr(app.task_memory, 'update_runtime', update_runtime)
    app.executor.claim()
    app.executor._current_command = {'command': 'mine', '_command_id': 'old'}
    return app, task


def test_router_replans_existing_executor_and_preserves_remaining_goal(runtime, monkeypatch):
    app, task = runtime

    async def scenario():
        plan = {'action': 'replan', 'commands': ['surface', 'mine iron 3', 'smelt raw_iron 3'],
                'expected_outcome': '取得並冶煉 3 個鐵', 'text': '換礦點'}
        monkeypatch.setattr(app, 'llm', type('LLM', (), {'chat': AsyncMock(return_value=json.dumps(plan))})())

        async def send(raw):
            msg = json.loads(raw)
            if msg['command'] == 'suspend':
                # Even matching stale completion cannot unblock a recovery.
                await app._on_done({'type': 'activity_done', 'activity': 'mining', 'command_id': 'old'}, None)
                assert app.executor.is_in_stuck_recovery()
                app.recovery.acknowledge({'request_id': msg['request_id'], 'stopped': True})

        ws = type('Socket', (), {'send': staticmethod(send)})()
        await app._handle_and_send({'type': 'activity_stuck', 'activity': 'mining', 'reason': 'no_progress',
                                    'pos': {'x': 0, 'y': 64, 'z': 0}}, app.recovery_skill.handle, ws)
        assert app.executor.is_running(), 'coordinator/executor retains ownership'
        assert app.executor._replan_commands == plan['commands']
        assert not app.executor.is_in_stuck_recovery()
        assert task['goal'] == '取得鐵'
        assert not app._thinking
        prompt = app.llm.chat.call_args.args[0][0]['content']
        assert 'smelt raw_iron 3' in prompt
    asyncio.run(scenario())


def test_router_holds_failed_decision_and_tick_cannot_replace_the_task(runtime, monkeypatch):
    app, task = runtime

    async def scenario():
        monkeypatch.setattr(app, 'llm', type('LLM', (), {'chat': AsyncMock(return_value='{"command":"chat","text":"thinking"}')})())
        sent = []

        async def send(raw):
            msg = json.loads(raw)
            sent.append(msg)
            if msg['command'] == 'suspend':
                app.recovery.acknowledge({'request_id': msg['request_id'], 'stopped': True})

        ws = type('Socket', (), {'send': staticmethod(send)})()
        await app._handle_and_send({'type': 'activity_stuck', 'activity': 'mining', 'reason': 'no_progress'}, app.recovery_skill.handle, ws)
        assert app.recovery.held
        assert task['interruptedBy'] == 'recovery_blocked'
        assert not app.executor.is_running()
        assert len([s for s in sent if s['command'] == 'chat']) == 1
        autonomous = AsyncMock()
        await app._handle_and_send({'type': 'tick', 'activity': 'idle'}, autonomous, ws)
        await app._handle_and_send({'type': 'food_low', 'activity': 'idle'}, autonomous, ws)
        autonomous.assert_not_called()
    asyncio.run(scenario())


def test_expected_stop_event_does_not_abort_multistep_plan(runtime):
    app, task = runtime

    async def scenario():
        app.executor._current_command = {
            'command': 'stopchop', '_command_id': 'stop-1'
        }
        await app._on_task_stopped({
            'type': 'task_stopped', 'activityName': 'chopping',
            'command_id': 'stop-1',
        }, None)
        assert app.executor.is_running()
        assert task['status'] == 'running'

    asyncio.run(scenario())


def test_late_task_stopped_from_previous_step_does_not_abort_next_step(runtime):
    app, task = runtime

    async def scenario():
        app.executor._current_command = {
            'command': 'stopsurface', '_command_id': 'stop-1'
        }
        app.executor.signal_done({
            'type': 'action_done', 'command_id': 'stop-1',
        })

        # The executor can advance before the paired task_stopped message is
        # dispatched by the event loop. It must still recognize the old ID.
        app.executor._current_command = {
            'command': 'equip', '_command_id': 'equip-1'
        }
        await app._on_task_stopped({
            'type': 'task_stopped', 'activityName': 'surface',
            'command_id': 'stop-1',
        }, None)

        assert app.executor.is_running()
        assert task['status'] == 'running'
        assert app.executor._current_command['command'] == 'equip'

    asyncio.run(scenario())


def test_self_task_only_rechecks_after_material_state_change(runtime, monkeypatch):
    app, _task = runtime

    async def scenario():
        app.executor.unclaim()
        app._planner_lock = asyncio.Lock()
        app._idle_started_at = 0
        app._last_self_task_at = 0
        app._last_self_task_fingerprint = None
        reset_session = Mock()
        fake_llm = type('LLM', (), {
            'is_available': lambda self: True,
            'unavailable_reason': lambda self: None,
            'reset_session': reset_session,
        })()
        monkeypatch.setattr(app, 'llm', fake_llm)
        calls = []

        async def autonomous(state, _llm):
            calls.append(state['food'])
            return None

        state = {
            'type': 'tick', 'activity': 'idle', 'food': 20, 'health': 20,
            'pos': {'x': 0, 'y': 64, 'z': 0}, 'inventory': [],
        }
        app._latest_state.clear()
        app._latest_state.update(state)
        await app._handle_and_send(state, autonomous, type('Socket', (), {})())
        app._last_self_task_at = 0
        await app._handle_and_send(dict(state), autonomous, type('Socket', (), {})())
        changed = {**state, 'food': 17}
        app._latest_state.clear()
        app._latest_state.update(changed)
        app._last_self_task_at = 0
        await app._handle_and_send(changed, autonomous, type('Socket', (), {})())

        assert calls == [20, 17]

    asyncio.run(scenario())


def test_interrupted_activity_memory_never_invents_a_missing_quantity(runtime, monkeypatch):
    app, _task = runtime
    remembered = Mock()
    updated = Mock()
    monkeypatch.setattr(app.task_memory, 'remember_interrupted', remembered)
    monkeypatch.setattr(app.task_memory, 'update_latest_interrupted_step_context', updated)

    app._save_current_task_to_memory({
        'stack': [{'activity': 'chopping', 'goal': {}, 'progress': {}}],
        'pos': {'x': 1, 'y': 64, 'z': 2},
    })
    remembered.assert_not_called()

    app._save_current_task_to_memory({
        'stack': [{
            'activity': 'chopping',
            'goal': {'logs': 5},
            'progress': {'logs': 2},
        }],
        'pos': {'x': 1, 'y': 64, 'z': 2},
    })
    assert remembered.call_args.args[1] == ['chop logs 3']


def test_food_wait_auto_resumes_only_after_hunger_recovers(runtime, monkeypatch):
    app, task = runtime

    async def scenario():
        task.update(
            status='interrupted',
            interruptedBy='waiting_for_food_change',
            runtime={'food_wait_resume_task': True},
        )
        monkeypatch.setattr(app.task_memory, 'load_any', lambda: task)
        monkeypatch.setattr(app, '_planner_lock', asyncio.Lock())
        app.executor.unclaim()
        launch = Mock()
        monkeypatch.setattr(app, '_launch_plan', launch)
        baseline = app.food_recovery.snapshot({
            'food': 9, 'inventory': [], 'entities': [],
            'nearby': {'animals': False, 'water': False, 'safe_surface': False},
        })
        app.recovery.hold_for_change('food', baseline, '沒有食物來源')
        ws = type('Socket', (), {'send': AsyncMock()})()

        unchanged = {
            'type': 'tick', 'activity': 'idle', 'food': 9,
            'inventory': [], 'entities': [],
            'nearby': {'animals': False, 'water': False, 'safe_surface': False},
        }
        assert await app._handle_waiting_for_change(unchanged, ws)
        launch.assert_not_called()
        assert app.recovery.phase == 'waiting_for_change'

        resolved = {**unchanged, 'food': 12}
        assert await app._handle_waiting_for_change(resolved, ws)
        assert launch.call_args.args[0] == ['resumetask']
        assert app.recovery.phase == 'executing'

    asyncio.run(scenario())


def test_food_wait_retries_once_when_animals_appear(runtime, monkeypatch):
    app, task = runtime

    async def scenario():
        task.update(
            status='interrupted',
            interruptedBy='waiting_for_food_change',
            runtime={'food_wait_resume_task': True},
        )
        monkeypatch.setattr(app.task_memory, 'load_any', lambda: task)
        routed = AsyncMock()
        monkeypatch.setattr(app, '_handle_and_send', routed)
        baseline = app.food_recovery.snapshot({
            'food': 9, 'inventory': [], 'entities': [],
            'nearby': {'animals': False, 'water': False, 'safe_surface': False},
        })
        app.recovery.hold_for_change('food', baseline, '沒有食物來源')
        ws = type('Socket', (), {'send': AsyncMock()})()

        changed = {
            'type': 'tick', 'activity': 'idle', 'food': 9,
            'inventory': [], 'entities': [],
            'nearby': {'animals': True, 'water': False, 'safe_surface': False},
        }
        assert await app._handle_waiting_for_change(changed, ws)
        assert app.recovery.phase == 'executing'
        assert task['interruptedBy'] == 'food_low'
        assert routed.call_args.args[0]['type'] == 'food_low'

    asyncio.run(scenario())


def test_standalone_food_wait_finishes_without_resuming_failed_plan(runtime, monkeypatch):
    app, task = runtime

    async def scenario():
        task.update(
            goal='補充食物',
            status='interrupted',
            interruptedBy='waiting_for_food_change',
            runtime={'food_wait_resume_task': False},
        )
        monkeypatch.setattr(app.task_memory, 'load_any', lambda: task)
        finished = Mock()
        monkeypatch.setattr(app.task_memory, 'done', finished)
        launch = Mock()
        monkeypatch.setattr(app, '_launch_plan', launch)
        baseline = app.food_recovery.snapshot({
            'food': 9, 'inventory': [], 'entities': [],
            'nearby': {'animals': False, 'water': False, 'safe_surface': False},
        })
        app.recovery.hold_for_change('food', baseline, '沒有食物來源')
        ws = type('Socket', (), {'send': AsyncMock()})()

        resolved = {
            'type': 'tick', 'activity': 'idle', 'food': 10,
            'inventory': [], 'entities': [],
            'nearby': {'animals': False, 'water': False, 'safe_surface': False},
        }
        assert await app._handle_waiting_for_change(resolved, ws)
        finished.assert_called_once_with(goal_verified=True)
        launch.assert_not_called()
        assert app.recovery.phase == 'executing'

    asyncio.run(scenario())


def test_standalone_food_retry_replaces_failed_plan_instead_of_resuming_it(runtime, monkeypatch):
    app, task = runtime

    async def scenario():
        task.update(
            goal='補充食物',
            status='interrupted',
            interruptedBy='food_low',
            runtime={'food_wait_resume_task': False},
        )
        monkeypatch.setattr(app.task_memory, 'load_any', lambda: task)
        monkeypatch.setattr(app, '_planner_lock', asyncio.Lock())
        app.executor.unclaim()
        launch = Mock()
        monkeypatch.setattr(app, '_launch_plan', launch)
        handler = AsyncMock(return_value=[{
            'action': 'plan',
            'commands': ['hunt count 1', 'getfood count 1'],
            'goal': '補充食物',
            'preserve_task': True,
        }])
        ws = type('Socket', (), {'send': AsyncMock()})()

        await app._handle_and_send(
            {'type': 'food_low', 'activity': 'idle', 'food': 9},
            handler,
            ws,
        )

        commands = launch.call_args.args[0]
        assert commands == ['hunt count 1', 'getfood count 1']
        assert launch.call_args.kwargs['preserve_task'] is False

    asyncio.run(scenario())
