"""Exercise the real event router with fake transport/LLM, without starting MC."""
import asyncio
import importlib
import json
from unittest.mock import AsyncMock

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
    monkeypatch.setattr(app.task_memory, 'update_runtime', lambda patch: task.update(runtime=patch))
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
        autonomous.assert_not_called()
    asyncio.run(scenario())
