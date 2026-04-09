from agent.skills.commands_ref import command_list
from agent.skills.state_summary import summarize_state


SYSTEM_PROMPT = f"""你是 Minecraft 機器人的狩獵卡住處理助手。
機器人在狩獵食物時遇到問題，請根據當前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"action": "replan", "commands": ["explore trees", "hunt count <n>"], "text": "...理由..."}}
{{"action": "replan", "commands": ["fish catches <n>", "getfood count <n>"], "text": "...理由..."}}
{{"action": "replan", "commands": ["chop logs 2", "hunt count <n>"], "text": "...理由..."}}
{{"action": "skip", "text": "...理由..."}}
{{"command": "chat", "text": "...提醒內容..."}}

【可用指令】
{command_list(["explore", "hunt", "fish", "getfood", "chop", "chat"])}

決策原則：
- reason 為 no_animals：附近已沒有可食用動物，不能把這次狩獵當成完成
- 若有釣竿可改用 fish catches <remaining>，再接回 getfood 與原計畫
- 若沒有釣竿，優先 explore trees 換到新的地表區域後，再 hunt count <remaining>
- 若 reason 為 no_weapon：目前沒有可用武器，而且本地合成流程已失敗，必須重新規劃，不要繼續原地追動物
- no_weapon 時，請優先根據 prompt 中的 candidate_routes / weapon_blockers / environment_hints 選擇：
  - `skip_hunt_if_food_already_enough`：如果熟食已足夠完成目前目標，直接 skip，不要再 hunt/getfood
  - `alternate_food_path`：若有釣竿，可改走 fish catches <remaining> → getfood count <remaining>
  - `wooden_sword_path`：若缺木頭鏈（logs/planks/sticks 都不足）但附近有樹，可先 chop logs 2 再回 hunt
  - `stone_sword_path`：若附近同時有樹與石頭，也可考慮先補木頭與石頭再回 hunt，但只有在你能明確接出完整剩餘步驟時才選
- 若 candidate_routes 顯示某條路線的 blocker 尚未解除，就不要選那條
- 若沒有明確可解的替代方案，寧可 chat 說明，也不要硬回會形成 dead loop 的指令
- 若目前在多步驟計畫中，replan 必須保留原本剩餘步驟，不能只回單一步驟
"""


def _extract_remaining(state: dict, plan_context: dict | None) -> int:
    remaining = state.get("remaining")
    if isinstance(remaining, int) and remaining > 0:
        return remaining
    current_cmd = ((plan_context or {}).get("current_cmd") or "").strip()
    parts = current_cmd.split()
    try:
        return int(parts[2]) if len(parts) >= 3 else 1
    except Exception:
        return 1


def describe_no_weapon_options(state: dict, plan_context: dict | None) -> dict:
    summary = summarize_state(state)
    resources = summary.get("resources") or {}
    food = resources.get("food") or {}
    wood = resources.get("wood") or {}
    env = summary.get("environment") or {}

    remaining = _extract_remaining(state, plan_context)
    cooked_total = int(food.get("cooked_total", 0) or 0)
    logs = int(wood.get("logs", 0) or 0)
    planks = int(wood.get("planks", 0) or 0)
    sticks = int(wood.get("sticks", 0) or 0)

    near_trees = bool(env.get("near_trees"))
    near_stone = bool(env.get("near_stone"))
    near_water = bool(env.get("near_water"))
    has_fishing_rod = any((item.get("name") == "fishing_rod") for item in (state.get("inventory") or []))

    block_keys = []
    if logs <= 0:
        block_keys.append("missing_logs")
    if planks <= 0:
        block_keys.append("missing_planks")
    if sticks <= 0:
        block_keys.append("missing_sticks")

    routes = []
    if cooked_total >= remaining:
        routes.append("skip_hunt_if_food_already_enough")
    if has_fishing_rod:
        routes.append("alternate_food_path")
    if near_trees and logs <= 0 and planks <= 0 and sticks <= 0:
        routes.append("wooden_sword_path")
    if near_trees and near_stone:
        routes.append("stone_sword_path")
    if not routes:
        routes.append("chat_for_help")

    return {
        "remaining": remaining,
        "cooked_total": cooked_total,
        "weapon_blockers": block_keys,
        "environment_hints": {
            "near_trees": near_trees,
            "near_stone": near_stone,
            "near_water": near_water,
        },
        "candidate_routes": routes,
    }


def _drop_leading_getfood_step(pending_steps: list[str], remaining: int) -> list[str]:
    if not pending_steps:
        return []
    first = (pending_steps[0] or "").strip()
    if first == f"getfood count {remaining}" or first.startswith("getfood "):
        return pending_steps[1:]
    return pending_steps


def build_replan_no_animals(state: dict, plan_context: dict) -> list[dict] | None:
    current_cmd = (plan_context.get("current_cmd") or "").strip()
    if not current_cmd.startswith("hunt "):
        return None

    remaining = state.get("remaining")
    if not isinstance(remaining, int) or remaining <= 0:
        parts = current_cmd.split()
        try:
            remaining = int(parts[2]) if len(parts) >= 3 else 1
        except Exception:
            remaining = 1

    pending_steps = plan_context.get("pending_steps", [])
    inventory = state.get("inventory") or []
    has_fishing_rod = any((item.get("name") == "fishing_rod") for item in inventory)

    if has_fishing_rod:
        commands = [f"fish catches {remaining}", f"getfood count {remaining}", *pending_steps]
        text = f"附近已沒有動物可獵，改用釣魚補足剩餘 {remaining} 份食物再接回原計畫。"
    else:
        commands = ["explore trees", f"hunt count {remaining}", *pending_steps]
        text = f"附近已沒有動物可獵，先換到新的地表區域，再補足剩餘 {remaining} 份生食。"

    return [
        {"command": "chat", "text": text},
        {"action": "replan", "commands": commands},
    ]


def deterministic_shortcut_no_weapon(state: dict, plan_context: dict | None) -> list[dict] | None:
    if state.get("reason") != "no_weapon":
        return None

    info = describe_no_weapon_options(state, plan_context)
    remaining = info["remaining"]
    pending_steps = (plan_context or {}).get("pending_steps", [])

    if info["cooked_total"] >= remaining:
        next_steps = _drop_leading_getfood_step(pending_steps, remaining)
        return [
            {"command": "chat", "text": f"背包熟食已足夠 {remaining} 份，直接跳過狩獵與補食流程。"},
            {"action": "replan", "commands": next_steps},
        ]

    if "alternate_food_path" in info["candidate_routes"]:
        commands = [f"fish catches {remaining}", f"getfood count {remaining}", *pending_steps]
        return [
            {"command": "chat", "text": f"目前做不出武器，但背包有釣竿，先改用釣魚補足剩餘 {remaining} 份食物。"},
            {"action": "replan", "commands": commands},
        ]
    return None
