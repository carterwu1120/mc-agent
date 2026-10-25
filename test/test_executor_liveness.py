import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from agent import executor as module


@pytest.mark.parametrize("command,reason", [("mine iron 3", "unexpected_idle"), ("equip", "action_timeout")])
def test_heartbeat_cannot_keep_a_dropped_command_waiting_forever(monkeypatch, command, reason):
    async def scenario():
        monkeypatch.setattr(module, "POLL_INTERVAL", 0.005)
        monkeypatch.setattr(module, "ACTIVITY_START_TIMEOUT", 0.01)
        monkeypatch.setattr(module, "ACTION_TIMEOUT", 0.01)
        e = module.PlanExecutor()
        e.update_state({"activity": "idle"})
        e.heartbeat()
        monkeypatch.setattr(module.task_memory, "interrupt", lambda *_: None)
        events = []

        async def recover(state, ws):
            events.append(state)
            e.abort(preserve_task=True)

        e._verify_failed_callback = recover
        ws = type("Socket", (), {"send": AsyncMock()})()
        await asyncio.wait_for(e.execute([command], ws, preserve_task=True), 1)
        assert [s["reason"] for s in events] == [reason]
        assert ws.send.await_count == 1, "do not blindly resend the same command"
        assert not e.is_running()
    asyncio.run(scenario())


def test_immediate_ack_is_not_lost_and_unrelated_completion_cannot_finish_step():
    async def scenario():
        e = module.PlanExecutor()
        seen = []

        async def send(raw):
            command = json.loads(raw)
            e.signal_done({"type": "action_done", "command_id": "old-command"})
            seen.append(e._done.is_set())
            e.signal_done({"type": "action_done", "command_id": command["_command_id"]})

        ws = type("Socket", (), {"send": staticmethod(send)})()
        await asyncio.wait_for(e.execute(["equip"], ws, preserve_task=True), 1)
        assert seen == [False]
        assert not e.is_running()
    asyncio.run(scenario())


def test_old_executor_finally_does_not_clear_replacement_command(monkeypatch):
    async def scenario():
        monkeypatch.setattr(module, "POLL_INTERVAL", 0.005)
        monkeypatch.setattr(module, "ACTION_TIMEOUT", 0.01)
        monkeypatch.setattr(module.task_memory, "interrupt", lambda *_: None)
        e = module.PlanExecutor()
        e.heartbeat()

        async def recover(state, ws):
            e.abort(preserve_task=True)
            e._current_command = {"command": "replacement"}

        e._verify_failed_callback = recover
        ws = type("Socket", (), {"send": AsyncMock()})()
        await asyncio.wait_for(e.execute(["equip"], ws, preserve_task=True), 1)
        assert e._current_command == {"command": "replacement"}
    asyncio.run(scenario())


def test_timeout_recovery_replaces_strategy_within_same_executor_run(monkeypatch):
    async def scenario():
        monkeypatch.setattr(module, 'POLL_INTERVAL', 0.005)
        monkeypatch.setattr(module, 'ACTIVITY_START_TIMEOUT', 0.01)
        monkeypatch.setattr(module.task_memory, 'load', lambda: None)
        e = module.PlanExecutor()
        e.heartbeat()
        e.update_state({'activity': 'idle'})
        sent = []

        async def recover(state, ws):
            e.notify_stuck()
            e.replan(['home'])

        async def send(raw):
            msg = json.loads(raw)
            sent.append(msg['command'])
            if msg['command'] in {'stopall', 'home'}:
                e.signal_done({'type': 'action_done', 'command_id': msg.get('_command_id')})

        e._verify_failed_callback = recover
        ws = type('Socket', (), {'send': staticmethod(send)})()
        await asyncio.wait_for(e.execute(['mine iron 3'], ws, preserve_task=True), 1)
        assert sent == ['mine', 'stopall', 'home']
        assert e._run_id == 1
        assert not e.is_running()
    asyncio.run(scenario())


def test_recovery_plan_after_unacknowledged_stopall_is_executed(monkeypatch):
    # stop_not_confirmed used to return from execute() with _running still set, so the
    # recovery plan handed to replan() was never run and the task looked busy forever.
    async def scenario():
        monkeypatch.setattr(module, "STOP_ACK_TIMEOUT", 0.01)
        monkeypatch.setattr(module.task_memory, "interrupt", lambda *_: None)
        e = module.PlanExecutor()
        e.heartbeat()
        e._pending_stopall = True
        sent = []

        async def send(raw):
            command = json.loads(raw)
            sent.append(command["command"])
            if command["command"] != "stopall" or sent.count("stopall") > 1:
                e.signal_done({"type": "action_done", "command_id": command.get("_command_id")})

        async def recover(state, ws):
            assert state["reason"] == "stop_not_confirmed"
            e.notify_stuck()
            e.replan(["home"])

        e._verify_failed_callback = recover
        ws = type("Socket", (), {"send": staticmethod(send)})()
        await asyncio.wait_for(e.execute(["mine iron 3"], ws, preserve_task=True), 1)
        assert sent == ["stopall", "stopall", "home"], sent
        assert not e.is_running()
    asyncio.run(scenario())


def test_unacknowledged_stopall_without_a_recovery_plan_stops_cleanly(monkeypatch):
    async def scenario():
        monkeypatch.setattr(module, "STOP_ACK_TIMEOUT", 0.01)
        monkeypatch.setattr(module.task_memory, "interrupt", lambda *_: None)
        e = module.PlanExecutor()
        e.heartbeat()
        e._pending_stopall = True

        async def recover(state, ws):
            pass  # e.g. recovery already busy with another event

        e._verify_failed_callback = recover
        ws = type("Socket", (), {"send": AsyncMock()})()
        await asyncio.wait_for(e.execute(["mine iron 3"], ws, preserve_task=True), 1)
        assert not e.is_running(), "a later recovery must be able to launch a fresh plan"
    asyncio.run(scenario())


def test_only_the_current_long_running_step_counts_as_progress():
    e = module.PlanExecutor()
    e._running = True
    e._current_command = {"command": "mine", "args": ["iron", "3"], "_command_id": "c1"}
    assert e.completes_current_activity({"type": "activity_done", "activity": "mining", "command_id": "c1"})
    assert not e.completes_current_activity({"type": "activity_done", "activity": "mining", "command_id": "old"})
    assert not e.completes_current_activity({"type": "activity_done", "activity": "surface", "command_id": "c1"})
    assert not e.completes_current_activity({"type": "action_done", "command_id": "c1"})
    e._current_command = {"command": "back", "_command_id": "c2"}
    assert not e.completes_current_activity({"type": "action_done", "command_id": "c2"}), "instant steps do not renew"


def test_replanned_step_records_the_stuck_reason(monkeypatch):
    failed = []
    monkeypatch.setattr(module.task_memory, "mark_step_failed", lambda step, error=None: failed.append(error))
    monkeypatch.setattr(module.task_memory, "record_event", lambda *a, **k: None)
    monkeypatch.setattr(module.task_memory, "replace_remaining_steps", lambda *a, **k: None)
    monkeypatch.setattr(module.task_memory, "load", lambda: None)
    e = module.PlanExecutor()
    e.replan(["explore animals", "hunt count 4"], path_taken="supervised_recovery", reason="no_animals")
    assert e._consume_replan(0, ["hunt count 4"], "hunt count 4", preserve_task=False) == ["explore animals", "hunt count 4"]
    assert failed == ["replanned:no_animals"]


def _inv(**items):
    return {"inventory": [{"name": k, "count": v} for k, v in items.items()]}


def test_hunt_verification_counts_java_raw_meat_ids():
    # Seen live: 4 chickens killed, yet "hunt 後肉類數量未增加" triggered a recovery.
    assert module._verify_step("hunt count 4", _inv(), _inv(chicken=4)) is None
    assert module._verify_step("hunt count 4", _inv(chicken=4), _inv(chicken=4)) is not None


def test_smelting_food_is_verified_by_its_cooked_output():
    # Seen live: smelt chicken 4 produced 4 cooked_chicken but was judged "+-4 chicken".
    assert module._verify_step("smelt chicken 4", _inv(chicken=8), _inv(chicken=4, cooked_chicken=4)) is None
    assert module._verify_step("smelt raw_iron 3", _inv(raw_iron=3), _inv(iron_ingot=3)) is None
