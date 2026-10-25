import asyncio
import json

from agent.handlers import self_task
from agent.handlers.self_task import (
    _food_decision_error,
    _normalize_result,
    _is_valid_command_result,
    _is_valid_plan_result,
)


class SequencedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, system=None, reasoning_effort=None):
        self.calls.append({
            "messages": messages,
            "system": system,
            "reasoning_effort": reasoning_effort,
        })
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def _empty_context(monkeypatch):
    monkeypatch.setattr(self_task.task_memory, "load", lambda: None)
    monkeypatch.setattr(self_task.task_memory, "recent_events", lambda: [])
    monkeypatch.setattr(self_task.task_memory, "recent_failures", lambda: [])
    monkeypatch.setattr(self_task.task_memory, "interrupted_tasks", lambda: [])
    monkeypatch.setattr(self_task.exploration_memory, "summary_for_prompt", lambda: "")


# ── _normalize_result ─────────────────────────────────────────────────────────

def test_normalize_getfood_does_not_invent_goal():
    assert _normalize_result({"command": "getfood"}) == {"command": "getfood"}


def test_normalize_chop_does_not_invent_goal():
    assert _normalize_result({"command": "chop"}) == {"command": "chop"}


def test_normalize_mine_does_not_invent_count():
    result = _normalize_result({"command": "mine", "args": ["diamond"]})
    assert result["args"] == ["diamond"]


def test_normalize_passthrough():
    r = {"command": "equip"}
    assert _normalize_result(r) == {"command": "equip"}


# ── _is_valid_command_result ──────────────────────────────────────────────────

def test_valid_getfood():
    assert _is_valid_command_result({"command": "getfood", "goal": {"count": 8}})


def test_invalid_getfood_missing_goal():
    assert not _is_valid_command_result({"command": "getfood"})


def test_valid_mine():
    assert _is_valid_command_result({"command": "mine", "args": ["diamond", "5"]})


def test_invalid_unknown_command():
    assert not _is_valid_command_result({"command": "fish"})


# ── _is_valid_plan_result ─────────────────────────────────────────────────────

def test_valid_plan():
    assert _is_valid_plan_result({"action": "plan", "commands": ["mine diamond 5", "equip"]})


def test_valid_food_plan_acquires_raw_food_first():
    decision = {
        "action": "plan",
        "commands": ["explore animals", "hunt count 4", "getfood count 4"],
    }
    assert _is_valid_plan_result(decision)
    assert _food_decision_error(decision, {"inventory": [], "food": 20}) is None


def test_bare_food_commands_are_invalid_plans():
    assert not _is_valid_plan_result({"action": "plan", "commands": ["hunt"]})
    assert not _is_valid_plan_result({"action": "plan", "commands": ["getfood"]})


def test_direct_getfood_rejected_without_enough_raw_food():
    decision = {"command": "getfood", "goal": {"count": 4}}
    error = _food_decision_error(decision, {"inventory": [], "food": 20})
    assert "raw_total=0" in error


def test_direct_getfood_allowed_with_enough_raw_food():
    decision = {"command": "getfood", "goal": {"count": 3}}
    state = {"inventory": [{"name": "beef", "count": 3}], "food": 12}
    assert _food_decision_error(decision, state) is None


def test_food_plan_rejected_when_acquisition_cannot_cover_target():
    decision = {
        "action": "plan",
        "commands": ["hunt count 2", "getfood count 5"],
    }
    error = _food_decision_error(decision, {"inventory": [], "food": 12})
    assert "計畫最多準備 2" in error


def test_fishing_rejected_without_rod():
    decision = {
        "action": "plan",
        "commands": ["fish catches 3", "getfood count 3"],
    }
    assert "fishing_rod" in _food_decision_error(decision, {"inventory": []})


def test_handle_reprompts_direct_getfood_without_raw_food(monkeypatch):
    _empty_context(monkeypatch)
    llm = SequencedLLM(
        {"command": "getfood", "goal": {"count": 6}, "text": "補充食物"},
        {
            "action": "plan",
            "commands": ["explore animals", "hunt count 6", "getfood count 6"],
            "goal": "依目前狀態準備食物",
            "text": "先取得生食再烹飪",
        },
    )
    state = {
        "activity": "idle",
        "mode": "survival",
        "food": 14,
        "inventory": [],
        "entities": [],
    }

    result = asyncio.run(self_task.handle(state, llm))

    assert result["commands"] == [
        "explore animals", "hunt count 6", "getfood count 6",
    ]
    assert len(llm.calls) == 2
    assert "raw_total=0" in llm.calls[1]["messages"][0]["content"]


def test_handle_allows_idle_at_full_hunger_without_food_stock(monkeypatch):
    _empty_context(monkeypatch)
    llm = SequencedLLM({"command": "idle", "text": "目前沒有迫切補給需求"})
    state = {
        "activity": "idle",
        "mode": "survival",
        "food": 20,
        "inventory": [],
        "entities": [],
    }

    assert asyncio.run(self_task.handle(state, llm)) is None
    assert len(llm.calls) == 1


def test_invalid_plan_empty_commands():
    assert not _is_valid_plan_result({"action": "plan", "commands": []})


def test_invalid_plan_disallowed_command():
    assert not _is_valid_plan_result({"action": "plan", "commands": ["fish 10"]})


def test_invalid_plan_wrong_action():
    assert not _is_valid_plan_result({"action": "drop", "commands": ["mine stone 5"]})
