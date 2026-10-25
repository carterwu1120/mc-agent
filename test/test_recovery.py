import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agent.recovery import RecoverySupervisor
from agent.handlers import recovery as skill


PLAN = {"action": "replan", "commands": ["chop logs 8", "mine iron 3"],
        "text": "取得材料再重試", "expected_outcome": "取得 8 個原木及 3 個鐵礦原料"}
STATE = {"activity": "mining", "reason": "no_progress", "pos": {"x": 0, "y": 64, "z": 0},
         "inventory": [], "plan_context": {"goal": "取得鐵"}}


class Socket:
    def __init__(self, supervisor, *, ack=True, stopped=True):
        self.supervisor = supervisor
        self.ack = ack
        self.stopped = stopped
        self.sent = []

    async def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if self.ack and msg["command"] == "suspend":
            self.supervisor.acknowledge({"request_id": msg["request_id"], "stopped": self.stopped,
                                         "pos": {"x": 1, "y": 64, "z": 0}})


def test_recovery_ack_then_decision_with_fresh_state_then_apply():
    async def scenario():
        r = RecoverySupervisor()
        ws = Socket(r)
        decide, apply, blocked = AsyncMock(return_value=PLAN), AsyncMock(), AsyncMock()
        await r.recover(STATE, ws, decide=decide, apply=apply, blocked=blocked)
        assert ws.sent[0]["command"] == "suspend"
        assert decide.call_args.args[0]["pos"]["x"] == 1
        apply.assert_awaited_once_with(PLAN)
        blocked.assert_not_called()
        assert r.status()["phase"] == "executing"
        assert r.expected == PLAN["expected_outcome"]
    asyncio.run(scenario())


@pytest.mark.parametrize("ack,stopped", [(False, True), (True, False)])
def test_no_confirmed_stop_means_no_ai_or_new_commands(ack, stopped):
    async def scenario():
        r = RecoverySupervisor(pause_timeout=0.01)
        decide, apply, blocked = AsyncMock(), AsyncMock(), AsyncMock()
        await r.recover(STATE, Socket(r, ack=ack, stopped=stopped), decide=decide, apply=apply, blocked=blocked)
        decide.assert_not_called()
        apply.assert_not_called()
        blocked.assert_awaited_once()
        assert r.held
    asyncio.run(scenario())


def test_invalid_decision_retries_once_then_waits_with_reason():
    async def scenario():
        r = RecoverySupervisor()
        decide, apply, blocked = AsyncMock(side_effect=ValueError("chat only")), AsyncMock(), AsyncMock()
        await r.recover(STATE, Socket(r), decide=decide, apply=apply, blocked=blocked)
        assert decide.await_count == 2
        assert "chat only" in decide.call_args.args[0]["decision_error"]
        apply.assert_not_called()
        assert r.held
        await r.recover(STATE, Socket(r), decide=decide, apply=apply, blocked=blocked)
        assert decide.await_count == 2, "held tasks must not spend tokens every tick"
    asyncio.run(scenario())


def test_hanging_decision_has_deadline_and_cleanup():
    async def scenario():
        r = RecoverySupervisor(decision_timeout=0.01)
        cancelled = []

        async def hangs(_state):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)

        blocked = AsyncMock()
        await asyncio.wait_for(r.recover(STATE, Socket(r), decide=hangs, apply=AsyncMock(), blocked=blocked), 1)
        assert len(cancelled) == 2
        assert r.held
        blocked.assert_awaited_once()
    asyncio.run(scenario())


def test_overlapping_reports_coalesce_and_new_instruction_invalidates_late_answer():
    async def scenario():
        r = RecoverySupervisor()
        started = asyncio.Event()

        async def late(_state):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return PLAN  # a provider may finish concurrently with cancellation

        apply, blocked = AsyncMock(), AsyncMock()
        ws = Socket(r)
        work = asyncio.create_task(r.recover(STATE, ws, decide=late, apply=apply, blocked=blocked))
        await started.wait()
        await r.recover(STATE, ws, decide=late, apply=apply, blocked=blocked)
        assert len(ws.sent) == 1
        r.invalidate()
        await work
        apply.assert_not_called()
        blocked.assert_not_called()
        assert not r.busy and not r.held
    asyncio.run(scenario())


def test_same_failed_strategy_is_rejected_and_changed_strategy_is_allowed():
    async def scenario():
        r = RecoverySupervisor()
        ws = Socket(r)
        apply, blocked = AsyncMock(), AsyncMock()
        await r.recover(STATE, ws, decide=AsyncMock(return_value=PLAN), apply=apply, blocked=blocked)
        different = {**PLAN, "commands": ["surface", "explore stone", "mine iron 3"]}
        decide = AsyncMock(side_effect=[PLAN, different])
        await r.recover(STATE, ws, decide=decide, apply=apply, blocked=blocked)
        assert decide.await_count == 2
        assert "same_failed_strategy" in decide.call_args.args[0]["decision_error"]
        assert apply.call_args.args[0] == different
        assert not r.held
    asyncio.run(scenario())


def test_recovery_storm_has_a_budget_even_if_each_plan_is_different():
    async def scenario():
        r = RecoverySupervisor()
        decide = AsyncMock(side_effect=[{**PLAN, "commands": [f"mine stone {n}"]} for n in range(1, 4)])
        apply, blocked = AsyncMock(), AsyncMock()
        for _ in range(4):
            await r.recover(STATE, Socket(r), decide=decide, apply=apply, blocked=blocked)
        assert decide.await_count == 3
        assert r.held and "budget_exhausted" in r.reason
    asyncio.run(scenario())


def test_unexpected_idle_only_when_there_is_unfinished_work():
    now = [0]
    r = RecoverySupervisor(clock=lambda: now[0])
    state = {"activity": "idle", "mc_connected": True, "mode": "companion"}
    task = {"id": "task", "status": "running", "currentStep": 0}
    assert r.unexpected_idle(state, task) is None
    now[0] = 16
    assert r.unexpected_idle(state, task)["reason"] == "unexpected_idle"
    assert r.unexpected_idle(state, {**task, "status": "interrupted"}) is None
    assert r.unexpected_idle(state, None) is None
    assert r.unexpected_idle({**state, "mc_connected": False}, task) is None
    r.phase = "waiting_ai"
    assert r.unexpected_idle(state, task) is None


def test_waiting_for_change_is_held_until_explicitly_released():
    r = RecoverySupervisor()
    snapshot = {"food_level": 9, "near_animals": False}

    r.hold_for_change("food", snapshot, "沒有食物來源")

    assert r.held
    assert r.status()["phase"] == "waiting_for_change"
    assert r.status()["waiting_for"] == "food"
    assert r.wait_snapshot == snapshot

    r.release_change_wait()
    assert not r.held
    assert r.status()["phase"] == "executing"
    assert r.wait_snapshot is None


@pytest.mark.parametrize("response", [None, {"command": "chat", "text": "thinking"},
    {"action": "replan", "commands": ["equip"], "text": "empty after normalization", "expected_outcome": "equipment"},
    {"action": "replan", "commands": ["mine iron"], "text": "bad", "expected_outcome": "iron"},
    {"action": "replan", "commands": ["mine iron 3"], "text": "missing expectation"}])
def test_skill_rejects_non_executable_decisions(response):
    async def scenario():
        llm = type("LLM", (), {"chat": AsyncMock(return_value=json.dumps(response))})()
        with pytest.raises(ValueError):
            await skill.handle(STATE, llm)
    asyncio.run(scenario())


def test_skill_preserves_explicit_wait_instead_of_silent_idle():
    async def scenario():
        answer = {"action": "wait", "text": "需要玩家提供可進入的路線"}
        llm = type("LLM", (), {"chat": AsyncMock(return_value=json.dumps(answer))})()
        assert await skill.handle(STATE, llm) == answer
    asyncio.run(scenario())


def test_real_progress_between_recoveries_renews_the_budget():
    # Seen live: no_blocks, no_input and no_animals were each resolved and the plan
    # kept finishing activities, yet the third unrelated problem exhausted the budget.
    async def scenario():
        r = RecoverySupervisor()
        ws = Socket(r)
        for index in range(5):
            plan = {**PLAN, "commands": [f"chop logs {index + 1}"]}
            await r.recover({**STATE, "reason": f"problem_{index}"}, ws,
                            decide=AsyncMock(return_value=plan), apply=AsyncMock(), blocked=AsyncMock())
            assert r.phase == "executing", f"recovery {index} was blocked"
            r.note_progress()
    asyncio.run(scenario())


def test_progress_does_not_allow_repeating_the_same_failed_strategy():
    async def scenario():
        r = RecoverySupervisor()
        ws = Socket(r)
        blocked = AsyncMock()
        await r.recover(STATE, ws, decide=AsyncMock(return_value=PLAN), apply=AsyncMock(), blocked=blocked)
        r.note_progress()
        await r.recover(STATE, ws, decide=AsyncMock(return_value=PLAN), apply=AsyncMock(), blocked=blocked)
        assert r.held and "same_failed_strategy" in r.reason
    asyncio.run(scenario())
