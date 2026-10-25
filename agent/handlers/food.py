import math

from agent.brain import LLMClient
from agent.handlers.state_summary import summarize_state

RISKY_ACTIVITIES = {'mining', 'combat', 'hunting', 'explore', 'fishing'}


def _build_food_plan(state: dict) -> tuple[list[str], int]:
    """Build an executable food plan from hunger, inventory and task risk."""
    summary = summarize_state(state)
    resources = summary['resources']['food']
    environment = summary['environment']
    food_level = max(0, min(20, int(state.get('food', 20) or 0)))
    cooked_total = int(resources.get('cooked_total', 0) or 0)
    raw_total = int(resources.get('raw_total', 0) or 0)
    activity = state.get('activity', 'idle')

    # Four hunger points per meal is deliberately conservative across food types.
    # agent/knowledge/bot_capabilities.md (食物不足時的數量) states this formula; keep them in sync.
    meals_for_hunger = math.ceil(max(0, 20 - food_level) / 4)
    risk_buffer = 1 if activity in RISKY_ACTIVITIES else 0
    target = max(1, meals_for_hunger + risk_buffer - cooked_total)

    if raw_total >= target:
        return [f"getfood count {target}"], target

    missing_raw = max(1, target - raw_total)
    inventory_names = {
        item.get('name') for item in (state.get('inventory') or []) if item.get('name')
    }
    commands: list[str] = []
    if 'fishing_rod' in inventory_names and environment.get('near_water'):
        commands.append(f"fish catches {missing_raw}")
    else:
        if not environment.get('has_near_animals'):
            commands.append("explore animals")
        commands.append(f"hunt count {missing_raw}")
    commands.append(f"getfood count {target}")
    return commands, target


async def handle(state: dict, llm: LLMClient) -> list | None:
    food = state.get("food", 20)
    activity = state.get("activity", "idle")
    commands, target = _build_food_plan(state)
    print(
        f"[Skill/food] 食物不足（{food}/20），activity={activity}，"
        f"動態目標={target}，計畫={commands}"
    )
    return [
        {"command": "chat", "text": f"食物不足（{food}/20），暫停目前任務去補充食物"},
        {"action": "plan", "commands": commands, "goal": "補充食物", "preserve_task": True},
    ]
