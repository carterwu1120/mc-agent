import json
import re
from agent import task_memory
from agent.brain import LLMClient
from agent.context_builder import build_for_skill
from agent.prompt_logging import log_prompt
from agent.handlers.llm_response import parse_llm_json
from agent.handlers.knowledge import knowledge
from agent.handlers.prompts import render_prompt
from agent.handlers.state_summary import equipment_summary

LABEL_PATTERNS = {
    "food": ["cooked_", "bread", "apple", "carrot", "potato", "beef", "pork", "chicken", "mutton", "rabbit", "salmon", "cod"],
    "wood": ["_log", "_planks"],
    "stone": ["cobblestone", "deepslate", "gravel", "sand", "diorite", "andesite", "granite", "tuff", "calcite"],
    "ore": ["_ingot", "raw_iron", "raw_gold", "raw_copper", "lapis_lazuli", "quartz", "diamond", "emerald", "coal"],
}

EQUIPMENT_SUFFIXES = (
    "_pickaxe", "_axe", "_sword", "_shovel", "_hoe", "_helmet", "_chestplate",
    "_leggings", "_boots", "_bow", "crossbow", "shield", "elytra", "trident",
)

SYSTEM_PROMPT = render_prompt("inventory", knowledge=knowledge("inventory"))


def _is_equipment(name: str) -> bool:
    return any(name.endswith(s) for s in EQUIPMENT_SUFFIXES) or name in {"crafting_table", "furnace"}


def _item_label(name: str) -> str | None:
    if _is_equipment(name):
        return None
    for label, patterns in LABEL_PATTERNS.items():
        if any(p in name for p in patterns):
            return label
    return "misc"


def _should_make_misc_chest(inventory: list[dict]) -> bool:
    counts: dict[str, int] = {}
    total = 0
    for item in inventory:
        label = _item_label(str(item.get("name", "")))
        if not label:
            continue
        counts[label] = counts.get(label, 0) + 1
        total += 1
    if total <= 0:
        return False
    if len(counts) >= 3:
        return True
    dominant = max(counts.values(), default=0)
    return dominant * 2 <= total


async def handle(state: dict, llm: LLMClient) -> dict | None:
    inventory = state.get("inventory", [])
    activity = state.get("activity", "idle")
    stack = state.get("stack", [])
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    y = round(pos.get("y", 0))
    chests = state.get("chests", [])

    def _fmt_inv_item(i):
        base = f"- {i['name']} x{i['count']}"
        pct = i.get('durability_pct')
        if pct is not None:
            base += f" (耐久 {pct}%)"
        return base
    inv_summary = "\n".join(_fmt_inv_item(i) for i in inventory)

    labeled_chests = [c for c in chests if c.get('label')]
    chests_summary = "\n".join(
        f"- id={c['id']} label={c.get('label','未分類')} freeSlots={c.get('freeSlots','?')}"
        for c in labeled_chests
    ) or "（無已分類箱子）"
    logs_count = sum(i['count'] for i in inventory if i['name'].endswith('_log'))
    planks_count = sum(i['count'] for i in inventory if i['name'].endswith('_planks'))
    # 1 chest = 8 planks, need 2 chests = 16 planks; logs convert 1:4 to planks
    wood_as_planks = planks_count + logs_count * 4
    chest_count = sum(i['count'] for i in inventory if i['name'] == 'chest')
    can_make_chest = wood_as_planks + chest_count * 8 >= 16
    if not labeled_chests:
        chests_summary += (
            f"\n（背包中有木材：{logs_count} logs + {planks_count} planks = {wood_as_planks} planks 等效"
            f"，現成 chest 物品：{chest_count} 個"
            f"，{'足夠' if can_make_chest else '不足'}製作箱子（需要等效 16 planks 或 2 個 chest 物品））"
        )

    has_home = bool(state.get("home"))
    if not labeled_chests and can_make_chest and _should_make_misc_chest(inventory):
        if has_home:
            return {
                "action": "plan",
                "commands": [
                    "home",
                    "makechest",
                    "labelchest {new_chest_id} misc",
                    "deposit {new_chest_id}",
                    "back",
                ],
            }
        else:
            return {
                "action": "plan",
                "commands": [
                    "surface",
                    "sethome",
                    "makechest",
                    "labelchest {new_chest_id} misc",
                    "deposit {new_chest_id}",
                    "back",
                ],
            }

    # Current goal/progress from stack for mine remaining calculation
    top = stack[-1] if stack else {}
    goal = top.get("goal", {})
    progress = top.get("progress", {})
    goal_str = f"目標：{goal}，進度：{progress}" if goal else "（無目標）"

    equip_summary = equipment_summary(state)
    slots = state.get("inventory_slots") or {}
    slots_used = slots.get("used", len(inventory))
    slots_free = slots.get("free", 36 - len(inventory))

    home_str = "已設定" if has_home else "未設定（不能使用 home 指令）"
    prompt = (
        f"背包狀態：{slots_used}/36 格已用，剩餘 {slots_free} 格。機器人目前的活動：{activity}，位置 Y={y}，血量={health}/20，飢餓={food}/20。\n"
        f"基地（home）：{home_str}\n"
        f"當前任務：{goal_str}\n\n"
        f"目前裝備欄（耐久度）：\n{equip_summary}\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"已登記箱子：\n{chests_summary}\n\n"
        f"請根據活動規則決定處理方式。"
        f"{build_for_skill('inventory', task_memory.recent_events(), task_memory.recent_failures())}"
    )

    response = None
    try:
        log_prompt("Skill/inventory", prompt)
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = parse_llm_json(json.loads(clean), "Skill/inventory")
        if decision.get('action') == 'plan':
            return decision
        return {"command": "inventory_decision", **decision}
    except Exception as e:
        print(f"[Skill/inventory] 解析失敗: {e}\n原始回應: {response!r}")
        return None
