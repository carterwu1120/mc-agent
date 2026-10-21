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
