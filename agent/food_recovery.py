"""Semantic change detection for a paused food-recovery episode."""
from __future__ import annotations

from agent.skills.state_summary import summarize_state


FOOD_CRISIS_RESOLVED_LEVEL = 10


def snapshot(state: dict) -> dict:
    summary = summarize_state(state)
    food = summary["resources"]["food"]
    environment = summary["environment"]
    inventory_names = {
        item.get("name")
        for item in (state.get("inventory") or [])
        if item.get("name")
    }
    nearby = state.get("nearby") or {}
    return {
        "food_level": int(state.get("food", 20) or 0),
        "cooked_total": int(food.get("cooked_total", 0) or 0),
        "raw_total": int(food.get("raw_total", 0) or 0),
        "near_animals": bool(environment.get("has_near_animals")),
        "can_fish": "fishing_rod" in inventory_names and bool(environment.get("near_water")),
        "safe_surface": bool(nearby.get("safe_surface")),
    }


def classify_change(baseline: dict, state: dict) -> dict | None:
    """Return an automatic transition only for a food-relevant improvement."""
    current = snapshot(state)

    if current["food_level"] >= FOOD_CRISIS_RESOLVED_LEVEL:
        return {
            "action": "resume_task",
            "reason": f"飢餓已回升至 {current['food_level']}/20",
            "snapshot": current,
        }

    opportunities = []
    if current["raw_total"] > int(baseline.get("raw_total", 0) or 0):
        opportunities.append("背包新增可處理的生食")
    if current["near_animals"] and not baseline.get("near_animals", False):
        opportunities.append("附近出現可取得食物的動物")
    if current["can_fish"] and not baseline.get("can_fish", False):
        opportunities.append("現在同時具備釣竿與水源")
    if current["safe_surface"] and not baseline.get("safe_surface", False):
        opportunities.append("目前位置已成為安全地表")

    if opportunities:
        return {
            "action": "retry_food",
            "reason": "、".join(opportunities),
            "snapshot": current,
        }

    # Inventory food is handled by the bot's immediate eating monitor. Merely
    # seeing the item must not race that monitor or launch another LLM plan.
    return None


def is_food_recovery_task(task: dict | None) -> bool:
    if not task:
        return False
    if task.get("interruptedBy") in {"food_low", "waiting_for_food_change"}:
        return True
    goal = str(task.get("goal") or "").strip().lower()
    return goal in {"補充食物", "food recovery", "get food"}
