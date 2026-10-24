import asyncio
import json

from agent.skills import craft_decision, respawn


class FakeLLM:
    def __init__(self, response: dict):
        self.response = json.dumps(response, ensure_ascii=False)
        self.calls = []

    async def chat(self, messages, system=None, reasoning_effort=None):
        self.calls.append({
            "messages": messages,
            "system": system,
            "reasoning_effort": reasoning_effort,
        })
        return self.response


def _empty_history(monkeypatch, module):
    monkeypatch.setattr(module.task_memory, "recent_events", lambda: [])
    monkeypatch.setattr(module.task_memory, "recent_failures", lambda: [])
    if hasattr(module.task_memory, "interrupted_tasks"):
        monkeypatch.setattr(module.task_memory, "interrupted_tasks", lambda: [])


def test_respawn_uses_validated_flexible_prerequisites(monkeypatch):
    _empty_history(monkeypatch, respawn)
    llm = FakeLLM({
        "action": "plan",
        "commands": ["explore animals", "hunt count 4"],
        "text": "空背包且附近沒有動物，先探索再取得食物。",
    })
    state = {
        "cause": "other",
        "remaining": ["getfood count 8"],
        "goal": "取得 8 份熟食",
        "taskCurrentCmd": "getfood count 8",
        "pos": {"x": 1, "y": 64, "z": 2},
        "deathPos": {"x": 10, "y": 64, "z": 20},
        "inventory": [],
        "health": 20,
        "food": 20,
        "nearby_entities": [],
    }

    result = asyncio.run(respawn.handle(state, llm))

    plan = result[1]
    assert plan["commands"] == ["explore animals", "hunt count 4", "resumetask"]
    assert plan["preserve_task"] is True
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "完整決策狀態" in prompt
    assert "capabilities" in prompt
    assert "environment" in prompt


def test_material_decision_can_build_multi_step_plan(monkeypatch):
    _empty_history(monkeypatch, craft_decision)
    monkeypatch.setattr(craft_decision.task_memory, "load", lambda: {"status": "running"})
    llm = FakeLLM({
        "action": "plan",
        "commands": ["chop logs 8", "mine iron 24"],
        "text": "先建立木材工具鏈，再取得鐵材料。",
    })
    state = {
        "reason": "material_missing",
        "goal": "iron_armor",
        "missing_materials": [{"name": "iron_ingot", "count": 24}],
        "activity": "craft_armor",
        "pos": {"x": 0, "y": 68, "z": 0},
        "inventory": [],
        "health": 20,
        "food": 20,
    }

    result = asyncio.run(craft_decision.handle(state, llm))

    plan = result[1]
    assert plan["commands"] == ["chop logs 8", "mine iron 24", "resumetask"]
    assert plan["preserve_task"] is True
    assert "capabilities" in llm.calls[0]["messages"][0]["content"]


def test_material_response_survives_expected_craft_activity_pop(monkeypatch):
    import agent.logger
    monkeypatch.setattr(agent.logger, "init_logger", lambda _service: "")
    from agent import agent as runtime

    request = {
        "type": "craft_decision",
        "reason": "material_missing",
        "activity": "craft_armor",
        "pos": {"x": 0, "y": 64, "z": 0},
        "health": 20,
        "inventory": [],
    }
    latest = {
        "type": "tick",
        "activity": "idle",
        "pos": {"x": 0, "y": 64, "z": 0},
        "health": 20,
        "inventory": [],
    }
    monkeypatch.setattr(runtime, "_latest_state", latest)
    assert runtime._is_stale_response("craft_decision", request) is False

    latest["inventory"] = [{"name": "iron_ingot", "count": 24}]
    assert runtime._is_stale_response("craft_decision", request) is True
