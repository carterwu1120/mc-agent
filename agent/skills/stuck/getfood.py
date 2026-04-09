from agent.skills.commands_ref import command_list
from agent.skills.state_summary import summarize_state


SYSTEM_PROMPT = f"""你是 Minecraft 機器人的食物補充卡住處理助手。
機器人在補充食物時遇到問題（背包沒有原始食材可冶煉），請決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"action": "replan", "commands": ["hunt count <n>", "getfood count <food_target>"], "text": "...理由..."}}
{{"action": "replan", "commands": ["fish catches <n>", "getfood count <food_target>"], "text": "...理由..."}}
{{"command": "chat", "text": "...提醒內容..."}}

【可用指令】
{command_list(["hunt", "fish", "getfood", "chat"])}

決策原則：
- reason 為 no_raw_food：背包沒有生食可冶煉，必須先取得生食再重啟 getfood
- 根據計畫目標（plan_context.goal）決定 food_target：
  - 短暫/輕鬆任務 → food_target = 8
  - 一般挖礦/砍樹 → food_target = 16
  - 長時間/危險任務（鑽石、深挖、combat、探索）→ food_target = 32
- hunt count = food_target（不要假設每隻動物平均掉 2 個原料，先採 1:1 保守估計）
- 若背包有釣竿（inventory 有 fishing_rod）→ 可改用 fish catches <food_target>，再 getfood count <food_target>
- 永遠用 replan 格式，不要只回單一指令
- **重要**：replan 的 commands 必須在 hunt+getfood 之後附加「原計畫剩餘步驟」，否則後續任務會被丟失
  例如：["hunt count <remaining>", "getfood count <remaining>", "mine iron 3", "smelt raw_iron 3", "equip", "mine diamond 10"]
  若後續只是補鐵鎬或補工具鏈，礦物與冶煉數量請依實際缺口估算，不要固定寫 16
- getfood count 必須用「還需熟食數量」（remaining），不是原本的總目標數
"""


def extract_count_from_command(cmd: str | None) -> int | None:
    if not cmd:
        return None
    parts = cmd.split()
    if len(parts) >= 3:
        try:
            return int(parts[2])
        except Exception:
            return None
    return None


def build_replan_from_smelting(state: dict, plan_context: dict) -> list[dict] | None:
    current_cmd = (plan_context.get("current_cmd") or "").strip()
    target_count = extract_count_from_command(current_cmd)
    if not target_count:
        return None

    inventory = state.get("inventory") or []
    cooked_total = sum(
        int(item.get("count", 0))
        for item in inventory
        if str(item.get("name", "")).startswith("cooked_") or item.get("name") in {"bread", "baked_potato"}
    )
    has_fishing_rod = any((item.get("name") == "fishing_rod") for item in inventory)
    remaining = max(1, int(target_count) - int(cooked_total))
    pending_steps = plan_context.get("pending_steps", [])

    if has_fishing_rod:
        commands = [f"fish catches {remaining}", f"getfood count {remaining}", *pending_steps]
        text = f"熟食還差 {remaining} 個，先補魚貨再回來完成食物準備。"
    else:
        commands = [f"hunt count {remaining}", f"getfood count {remaining}", *pending_steps]
        text = f"熟食還差 {remaining} 個，先補生食再繼續後面的挖礦計畫。"

    return [
        {"command": "chat", "text": text},
        {"action": "replan", "commands": commands},
    ]


def build_replan_after_failed_hunt(state: dict, plan_context: dict) -> list[dict] | None:
    current_cmd = (plan_context.get("current_cmd") or "").strip()
    if not current_cmd.startswith("getfood "):
        return None

    remaining = state.get("remaining")
    if not isinstance(remaining, int) or remaining <= 0:
        remaining = extract_count_from_command(current_cmd) or 1

    pending_steps = plan_context.get("pending_steps", [])
    inventory = state.get("inventory") or []
    has_fishing_rod = any((item.get("name") == "fishing_rod") for item in inventory)

    if has_fishing_rod:
        commands = [f"fish catches {remaining}", f"getfood count {remaining}", *pending_steps]
        text = f"剛才狩獵區域已經沒有動物，改用釣魚補足剩餘 {remaining} 份食物再接回原計畫。"
    else:
        commands = ["explore trees", f"hunt count {remaining}", f"getfood count {remaining}", *pending_steps]
        text = f"剛才狩獵區域已經沒有動物，先換到新的地表區域，再補足剩餘 {remaining} 份食物。"

    return [
        {"command": "chat", "text": text},
        {"action": "replan", "commands": commands},
    ]


def deterministic_shortcut_no_raw_food_satisfied(state: dict, plan_context: dict | None) -> list[dict] | None:
    if state.get("reason") != "no_raw_food":
        return None

    current_cmd = ((plan_context or {}).get("current_cmd") or "").strip()
    if not current_cmd.startswith("getfood "):
        return None

    remaining = state.get("remaining")
    if not isinstance(remaining, int) or remaining <= 0:
        remaining = extract_count_from_command(current_cmd) or 1

    summary = summarize_state(state)
    cooked_total = int((((summary.get("resources") or {}).get("food") or {}).get("cooked_total", 0)) or 0)
    if cooked_total < remaining:
        return None

    pending_steps = list(((plan_context or {}).get("pending_steps") or []))
    return [
        {"command": "chat", "text": f"背包熟食已足夠 {remaining} 份，不需要再取得或冶煉食物，直接接回後續計畫。"},
        {"action": "replan", "commands": pending_steps},
    ]


def recent_hunting_no_animals(state: dict) -> bool:
    recent = state.get("recent_stuck") or []
    for item in reversed(recent):
        if not isinstance(item, dict):
            continue
        if item.get("activity") == "hunting" and item.get("reason") == "no_animals":
            return True
    return False
