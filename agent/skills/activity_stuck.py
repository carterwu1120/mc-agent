import json
import re
from agent.brain import LLMClient
from agent.skills.command_validation import (
    PLAN_ALLOWED_COMMANDS,
    build_reprompt_suffix,
    validate_commands,
)
from agent.skills.state_summary import summary_json
from agent.skills.commands_ref import command_list

ALLOWED_COMMANDS = {
    "chop", "mine", "chat", "idle", "home", "back", "surface", "explore", "withdraw", "fishing_decision"
}

PLAN_CONTEXT_SUFFIX = """
【當前執行計畫】處於多步驟計畫執行中。除了常規決策外，也可以回傳：

重新規劃（替換剩餘步驟）：
{"action": "replan", "commands": ["new step 1", "new step 2"], "text": "...理由..."}

跳過當前步驟（無法恢復且繼續剩餘步驟有意義）：
{"action": "skip", "text": "...理由..."}

只有在單步恢復無法解決問題時才使用 replan 或 skip。
"""


def _extract_first_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = decoder.raw_decode(text[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    raise json.JSONDecodeError("No valid JSON object found", text, 0)


def _parse_json_with_repair(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        return _extract_first_json_object(text)
    except json.JSONDecodeError:
        pass

    stripped = text.strip()
    if stripped.startswith("{"):
        open_braces = stripped.count("{")
        close_braces = stripped.count("}")
        if open_braces > close_braces:
            repaired = stripped + ("}" * (open_braces - close_braces))
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
            try:
                return _extract_first_json_object(repaired)
            except json.JSONDecodeError:
                pass

    # Salvage a common failure mode where the model starts with a valid
    # {"command": "...", "text": "..."} object and then appends unrelated prose
    # before closing the JSON object properly.
    command_match = re.search(r'"command"\s*:\s*"([^"]+)"', text)
    if command_match:
        salvaged = {"command": command_match.group(1)}

        text_key = text.find('"text"')
        if text_key != -1:
            first_quote = text.find('"', text_key + len('"text"'))
            if first_quote != -1:
                second_quote = text.find('"', first_quote + 1)
                if second_quote != -1:
                    text_start = second_quote + 1
                    text_end = text.find('"', text_start)
                    if text_end != -1:
                        salvaged["text"] = text[text_start:text_end]

        args_match = re.search(r'"args"\s*:\s*\[\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?', text)
        if args_match:
            salvaged["args"] = [v for v in args_match.groups() if v is not None]

        action_match = re.search(r'"action"\s*:\s*"([^"]+)"', text)
        if action_match:
            salvaged["action"] = action_match.group(1)

        x_match = re.search(r'"x"\s*:\s*(-?\d+(?:\.\d+)?)', text)
        if x_match:
            salvaged["x"] = float(x_match.group(1))

        z_match = re.search(r'"z"\s*:\s*(-?\d+(?:\.\d+)?)', text)
        if z_match:
            salvaged["z"] = float(z_match.group(1))

        logs_match = re.search(r'"logs"\s*:\s*(\d+)', text)
        if logs_match:
            salvaged["goal"] = {"logs": int(logs_match.group(1))}

        if salvaged.get("command"):
            return salvaged

    raise json.JSONDecodeError("No valid JSON object found", text, 0)


def _normalize_decision(activity: str, reason: str, needed_for: str | None, missing: list, missing_count: int | None, decision: dict) -> dict:
    if decision.get("command") == "chop" and "goal" not in decision and "args" not in decision:
        # Fallback only when the LLM omitted a quantity; prefer the LLM to decide goal.logs explicitly.
        if activity == "smelting" and reason == "missing_dependency":
            if needed_for == "pickaxe" or any(m in ("wood", "crafting_material", "stick") for m in missing):
                decision = {**decision, "goal": {"logs": 4}}
    if activity == "smelting" and reason == "missing_dependency" and "cobblestone" in missing:
        desired_count = str(missing_count or 8)
        args = decision.get("args")
        if decision.get("command") == "mine":
            if not args:
                decision = {**decision, "args": ["stone", desired_count]}
            elif len(args) == 1:
                target = args[0]
                if target != "stone":
                    target = "stone"
                decision = {**decision, "args": [target, desired_count]}
            elif len(args) >= 2:
                target = args[0]
                count = args[1]
                if target != "stone":
                    target = "stone"
                try:
                    count_num = int(count)
                except Exception:
                    count_num = 0
                if count_num < int(desired_count):
                    count = desired_count
                decision = {**decision, "args": [target, str(count)]}
    return decision


def _should_prefer_replan(activity: str, reason: str, plan_context: dict | None) -> bool:
    return activity == "mining" and reason == "no_tools" and bool(plan_context)


def _extract_count_from_command(cmd: str | None) -> int | None:
    if not cmd:
        return None
    parts = cmd.split()
    if len(parts) >= 3:
        try:
            return int(parts[2])
        except Exception:
            return None
    return None


def _looks_like_getfood_subflow(activity: str, reason: str, plan_context: dict | None) -> bool:
    if activity != "smelting" or reason != "no_input" or not plan_context:
        return False
    current_cmd = (plan_context.get("current_cmd") or "").strip()
    return current_cmd.startswith("getfood ")


def _build_getfood_replan_from_smelting(state: dict, plan_context: dict) -> list[dict] | None:
    current_cmd = (plan_context.get("current_cmd") or "").strip()
    target_count = _extract_count_from_command(current_cmd)
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


def _build_hunting_replan_no_animals(state: dict, plan_context: dict) -> list[dict] | None:
    current_cmd = (plan_context.get("current_cmd") or "").strip()
    if not current_cmd.startswith("hunt "):
        return None

    remaining = state.get("remaining")
    if not isinstance(remaining, int) or remaining <= 0:
        remaining = _extract_count_from_command(current_cmd) or 1

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


def _recent_hunting_no_animals(state: dict) -> bool:
    recent = state.get("recent_stuck") or []
    for item in reversed(recent):
        if not isinstance(item, dict):
            continue
        if item.get("activity") == "hunting" and item.get("reason") == "no_animals":
            return True
    return False


def _build_getfood_replan_after_failed_hunt(state: dict, plan_context: dict) -> list[dict] | None:
    current_cmd = (plan_context.get("current_cmd") or "").strip()
    if not current_cmd.startswith("getfood "):
        return None

    remaining = state.get("remaining")
    if not isinstance(remaining, int) or remaining <= 0:
        remaining = _extract_count_from_command(current_cmd) or 1

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


async def _reprompt_invalid_replan(
    llm: LLMClient,
    prompt: str,
    system: str,
    invalid_commands: list[str],
    errors,
) -> dict | None:
    reprompt = prompt + build_reprompt_suffix(
        invalid_commands=invalid_commands,
        errors=errors,
        allowed_command_keys=PLAN_ALLOWED_COMMANDS,
    )
    try:
        print(f"[Skill/activity_stuck] 偵測到非法 replan，重問一次 LLM：{invalid_commands}")
        response = await llm.chat(
            [{"role": "user", "content": reprompt}],
            system=system,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = _parse_json_with_repair(clean)
        if decision.get("action") != "replan":
            return None
        repair_errors = validate_commands(
            decision.get("commands", []),
            allowed_commands=PLAN_ALLOWED_COMMANDS,
        )
        if repair_errors:
            print(f"[Skill/activity_stuck] 修正後 replan 仍不合法：{[e.command for e in repair_errors]}")
            return None
        return decision
    except Exception as e:
        print(f"[Skill/activity_stuck] 修正 replan 失敗: {e}")
        return None


async def _reprompt_for_replan_strategy(
    llm: LLMClient,
    prompt: str,
    system: str,
    decision: dict,
    pending_steps: list[str],
) -> dict | None:
    reprompt = (
        prompt
        + "\n\n你上一個回覆用了單一步驟修復，但目前存在未完成的多步驟計畫。"
        + " 這種情況不能只回單一指令，必須在理解目前 activity、卡住原因、剩餘步驟與狀態後，"
        + " 回覆完整剩餘計畫的 replan，或明確回 skip。\n"
        + f"你上一個回覆是：{json.dumps(decision, ensure_ascii=False)}\n"
        + f"目前原計畫剩餘步驟：{pending_steps}\n"
        + "請只回覆以下其中一種 JSON：\n"
        + '{"action":"replan","commands":["...完整剩餘步驟..."],"text":"...理由..."}\n'
        + '{"action":"skip","text":"...理由..."}\n'
        + "不要回單一步驟 command。"
    )
    try:
        print("[Skill/activity_stuck] 需要完整 replan，重問一次 LLM")
        response = await llm.chat(
            [{"role": "user", "content": reprompt}],
            system=system,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        repaired = _parse_json_with_repair(clean)
        if repaired.get("action") == "replan":
            repair_errors = validate_commands(
                repaired.get("commands", []),
                allowed_commands=PLAN_ALLOWED_COMMANDS,
            )
            if repair_errors:
                print(f"[Skill/activity_stuck] 完整 replan 仍不合法：{[e.command for e in repair_errors]}")
                return None
            return repaired
        if repaired.get("action") == "skip":
            return repaired
        return None
    except Exception as e:
        print(f"[Skill/activity_stuck] 重問完整 replan 失敗: {e}")
        return None


def _replan_fallback(text: str) -> list[dict]:
    return [
        {"command": "chat", "text": text},
        {"action": "skip"},
    ]


def _is_valid_decision(decision: dict) -> bool:
    command = decision.get("command")
    if command not in ALLOWED_COMMANDS:
        return False

    if command == "mine":
        args = decision.get("args") or []
        return (
            isinstance(args, list)
            and len(args) >= 2
            and isinstance(args[0], str)
            and isinstance(args[1], str)
        )

    if command == "chop":
        goal = decision.get("goal") or {}
        args = decision.get("args") or []
        return (
            (isinstance(goal, dict) and isinstance(goal.get("logs"), int | float))
            or (isinstance(args, list) and len(args) >= 1)
        )

    if command == "withdraw":
        args = decision.get("args") or []
        return isinstance(args, list) and len(args) >= 2

    if command == "explore":
        args = decision.get("args") or []
        goal = decision.get("goal") or {}
        return (
            (isinstance(args, list) and len(args) >= 1 and isinstance(args[0], str))
            or (isinstance(goal, dict) and isinstance(goal.get("target"), str))
        )

    if command == "fishing_decision":
        action = decision.get("action")
        if action == "stop":
            return True
        if action == "move":
            return isinstance(decision.get("x"), int | float) and isinstance(decision.get("z"), int | float)
        return False

    return True

SYSTEM_PROMPTS = {
    "mining": f"""你是 Minecraft 機器人的挖礦卡住處理助手。
機器人在挖礦時遇到障礙而中斷，請根據目前的 activity、卡住原因、是否存在未完成計畫，以及當前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"action": "replan", "commands": ["chop logs 4", "mine iron 3", "smelt raw_iron 3", "equip", "mine diamond 10"], "text": "...理由..."}}
{{"action": "skip", "text": "...理由..."}}
{{"command": "chop", "text": "...理由..."}}
{{"command": "mine", "args": ["iron", "8"], "text": "...理由..."}}
{{"command": "chat", "text": "...需要玩家幫助的說明..."}}
{{"command": "idle", "text": "...理由..."}}

【可用指令】
{command_list(["chop", "mine", "chat", "idle"])}

決策原則：
- 若存在未完成 plan，且目前步驟是挖礦時因 no_tools 卡住，優先回覆 replan 或 skip，不要只回單一步驟 chop
- replan 必須是「從當前步驟開始的完整剩餘步驟」，可以插入修復步驟，但必須把原本剩餘計畫接回來
- 不要把 equip 當成萬用修復步驟；只有在前一步真的會產生新裝備（例如 smelt raw_iron 3 之後）時才加 equip
- 不要產生 equip、equip，或 chop 之後立刻接 equip 這種沒有新裝備可切換的序列
- 若原因為「無稿子且無法合成」：
  - 背包缺木材 → replan 插入 chop logs <n>，之後接回「補剛好夠用的工具鏈」與原剩餘步驟
  - 有木材但缺石稿/鐵鎬 → replan 插入補工具步驟，再接回原剩餘步驟
  - 補工具時採缺多少補多少，不要預設固定輸出 mine iron 16 / smelt raw_iron 16
- 只有在沒有未完成 plan、或這只是局部臨時修復時，才可以回單一步驟 chop / mine
- 若原因為「四個方向都被基岩或不可挖方塊阻擋，機器人可能被困住」→ 用 chat 告知玩家機器人被困，請玩家用 /tp 解救
- 其他情況 → idle
""",

    "smelting": f"""你是 Minecraft 機器人的燒製卡住處理助手。
機器人在燒製過程中遇到問題而中斷，請根據當前資源與整體計畫目標決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"action": "replan", "commands": ["chop logs 8", "smelt <target> <count>"], "text": "...理由..."}}
{{"action": "skip", "text": "...理由..."}}
{{"command": "chop", "goal": {{"logs": 8}}, "text": "...理由..."}}
{{"command": "mine", "args": ["<target>", "<count>"], "text": "...理由..."}}
{{"command": "home", "text": "...理由..."}}
{{"command": "withdraw", "args": ["oak_log", "16", "1"], "text": "...理由..."}}
{{"command": "chat", "text": "...提醒內容..."}}
{{"command": "idle", "text": "...理由..."}}

【可用指令】
{command_list(["mine", "chop", "home", "withdraw", "chat", "idle"])}

【no_fuel 決策邏輯（背包沒有任何可用燃料）】
先看整體計畫目標（plan_context.goal）和剩餘步驟（pending_steps）：

情境 A：下一步是挖礦（pending_steps 含 mine iron/diamond/coal 等）
→ 挖礦途中幾乎必定挖到煤礦 → 直接 skip 這個冶煉步驟，挖到煤後可以繼續
→ 使用 {{"action": "skip"}}

情境 B：背包有木頭（inventory 有 oak_log / planks）但量不夠燒完全部
→ 先用現有木頭燒一部分，剩下等挖礦拿到煤再繼續
→ replan：["chop logs <N>", "smelt <target> <count>"] 或直接 skip 讓挖礦途中解決

情境 C：計畫不含挖礦、背包也沒有木頭
→ 去砍樹取得燃料再繼續冶煉
→ replan：["chop logs 8", "smelt <target> <count>"]

【missing_dependency / no_input 決策邏輯】
- 若 missing 包含 wood → chop（估算 goal.logs 數量）
- 若 missing 是 cobblestone → mine stone <missing_count>
- 若背包有 iron_ingot >= 3 但沒有 iron_pickaxe → chat 提醒玩家合成
- 若背包資源足夠 → mine diamond
- 其他情況 → idle
- 禁止回覆 fish、smelt
""",

    "chopping": f"""你是 Minecraft 機器人的砍樹卡住處理助手。
機器人在砍樹時附近找不到可砍的樹，請根據目前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"command": "back", "text": "...理由..."}}
{{"command": "surface", "text": "...理由..."}}
{{"command": "explore", "args": ["trees"], "text": "...理由..."}}
{{"command": "home", "text": "...理由..."}}
{{"command": "chat", "text": "...提醒內容..."}}
{{"command": "idle", "text": "...理由..."}}

【可用指令】
{command_list(["back", "surface", "explore", "home", "chat", "idle"])}

決策原則：
- 若目前明顯在地底或附近沒有樹，但這次任務仍是砍樹，優先用 surface；若不確定 surface 是否可行，再用 back 回到先前位置
- 若已經在地表但附近沒有樹，優先回覆 explore trees，移動到新的地表區域繼續砍樹任務
- 若已設定 home 且判斷回基地更合理，可用 home
- 若沒有明確安全的下一步，才用 chat 或 idle
- 不要回 chop；目前 chopping activity 已經卡住，先脫離目前位置再說
- 不要只因為現在是夜晚、白天、天色變化，就選擇 home、idle 或放棄任務
- 只有在 prompt 中有明確危險證據（例如 danger_score 很高、附近 hostile、血量/飢餓危險）時，才可以把安全性當成主要理由
""",

    "surface": f"""你是 Minecraft 機器人的回到地表卡住處理助手。
機器人在前往地表時因路徑或地形問題中斷，請根據目前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"command": "back", "text": "...理由..."}}
{{"command": "home", "text": "...理由..."}}
{{"command": "chat", "text": "...提醒內容..."}}
{{"command": "idle", "text": "...理由..."}}

【可用指令】
{command_list(["back", "home", "chat", "idle"])}

決策原則：
- 若目前有可用的上一個位置，優先用 back
- 若已設定 home 且回基地更安全或更可靠，可用 home
- 若沒有明確安全的下一步，才用 chat 或 idle
- 不要再次回覆 surface，避免在相同條件下重複失敗
- 不要只因為現在是夜晚、白天、天色變化，就選擇 home、idle 或放棄任務
- 只有在 prompt 中有明確危險證據（例如 danger_score 很高、附近 hostile、血量/飢餓危險）時，才可以把安全性當成主要理由
""",

    "fishing": """你是 Minecraft 機器人的釣魚卡住處理助手。
機器人因拋竿方向或站位問題無法正常釣魚，請根據當前地圖與狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "fishing_decision", "action": "move", "x": 102, "z": -45, "text": "...理由..."}
{"command": "fishing_decision", "action": "stop", "text": "...理由..."}
{"command": "chat", "text": "...提醒內容..."}
{"command": "idle", "text": "...理由..."}

地圖說明：B=Bot目前位置, W=水, .=可走的陸地, #=阻擋, ~=懸崖
決策原則：
- 若附近仍有可釣水域，優先回覆 fishing_decision move，x/z 必須落在可走陸地
- 選靠近 W 的 . 格，避免選到 W、#、~ 格
- 若附近根本沒有合適站位，才用 fishing_decision stop 或 chat
- 不要回 fish；釣魚中已在原 activity 內，請只給 move/stop 類決策
""",
}

SYSTEM_PROMPTS["getfood"] = f"""你是 Minecraft 機器人的食物補充卡住處理助手。
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

SYSTEM_PROMPTS["hunting"] = f"""你是 Minecraft 機器人的狩獵卡住處理助手。
機器人在狩獵食物時遇到問題，請根據當前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"action": "replan", "commands": ["explore trees", "hunt count <n>"], "text": "...理由..."}}
{{"action": "replan", "commands": ["fish catches <n>", "getfood count <n>"], "text": "...理由..."}}
{{"command": "chat", "text": "...提醒內容..."}}

【可用指令】
{command_list(["explore", "hunt", "fish", "getfood", "chat"])}

決策原則：
- reason 為 no_animals：附近已沒有可食用動物，不能把這次狩獵當成完成
- 若有釣竿可改用 fish catches <remaining>，再接回 getfood 與原計畫
- 若沒有釣竿，優先 explore trees 換到新的地表區域後，再 hunt count <remaining>
- 若目前在多步驟計畫中，replan 必須保留原本剩餘步驟，不能只回單一步驟
"""

SYSTEM_PROMPTS["makechest"] = f"""你是 Minecraft 機器人的箱子製作問題處理助手。
機器人嘗試製作並放置箱子但失敗了，請根據當前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"action": "replan", "commands": ["deposit <existing_chest_id>"], "text": "...理由..."}}
{{"action": "replan", "commands": ["makechest", "labelchest {{{{new_chest_id}}}} misc", "deposit {{{{new_chest_id}}}}"], "text": "...理由..."}}
{{"action": "replan", "commands": ["chop logs 16", "makechest", "labelchest {{{{new_chest_id}}}} misc", "deposit {{{{new_chest_id}}}}"], "text": "...理由..."}}
{{"command": "chat", "text": "...需要玩家幫助的說明..."}}
{{"command": "idle", "text": "...理由..."}}

【可用指令】
{command_list(["chop", "makechest", "labelchest", "deposit", "chat", "idle"])}

【決策原則】（依優先順序）

1. 若 prompt 中有「已登記箱子」（known_chests > 0）且有空位（freeSlots > 0）
   → 優先直接 deposit 到現有箱子（不需要再 makechest）
   → replan: ["deposit <id>"]
   → 選 misc 箱子（label=misc）或有空間的任意箱子，用實際 id 數字

2. 若無可用現有箱子，但有足夠木材（planks ≥ 16 或 logs ≥ 2）
   → replan 直接重試 makechest + labelchest + deposit

3. 若無可用箱子且缺木材
   → replan 先砍樹再 makechest

4. 若背包無法整理（無現有箱子、無材料、背包滿）
   → chat 告知玩家

- labelchest 和 deposit 的 {{new_chest_id}} 是佔位符，makechest 完成後自動填入，不要替換成數字
- 若是 replan 中有 pending_steps（原計畫剩餘步驟），必須把它們附加在 deposit 之後
"""

SYSTEM_PROMPT_FALLBACK = """你是 Minecraft 機器人的問題處理助手。
機器人在執行任務時遇到問題而中斷，請根據當前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "chat", "text": "...需要玩家幫助的說明..."}
{"command": "idle", "text": "...理由..."}

若沒有明確可行的下一步，或需要玩家介入，用 chat 說明狀況，否則 idle。
"""

REASON_DESC = {
    'no_blocks': '四個方向都被基岩或不可挖方塊阻擋，機器人可能被困住',
    'no_tools':  '無稿子且目前無法完成工具準備',
    'no_input':  '背包中沒有可燒製的原料',
    'no_fuel':   '沒有可用的燃料',
    'missing_dependency': '缺少執行目前活動所需的前置資源或工具',
    'cannot_cook_food': '有生食但目前無法完成烹飪流程',
    'bad_cast': '拋竿角度或站位不佳，無法正常落水',
    'no_bobber': '拋竿後持續找不到浮標，可能站位或拋竿位置異常',
    'no_trees': '附近找不到可砍的樹，可能目前位置不適合進行砍樹',
    'no_progress': '活動持續一段時間沒有任何進展，可能卡住了',
    'timeout':   '操作超時',
    'no_animals': '附近已找不到可食用動物，狩獵未達目標',
    'has_raw_food': '背包有生食需要冶煉，重新規劃以冶煉後繼續',
}


def _build_fishing_prompt(state: dict, health, food) -> str:
    pos = state.get("pos", {})
    water = state.get("waterTarget")
    map_data = state.get("areaMap")

    map_section = "（無地圖資料）"
    if isinstance(map_data, dict) and "grid" in map_data:
        grid = map_data["grid"]
        origin_x = map_data["originX"]
        origin_z = map_data["originZ"]
        x_labels = "     " + "".join(f"{origin_x + i:2d}" for i in range(len(grid[0])))
        rows = [x_labels]
        for i, row in enumerate(grid):
            z = origin_z + i
            rows.append(f"{z:4d}: {''.join(f' {c}' for c in row)}")
        map_section = "\n".join(rows)
    elif isinstance(map_data, str) and map_data:
        map_section = map_data

    return (
        f"機器人在執行「fishing」時中斷（原因：{REASON_DESC.get(state.get('reason', 'unknown'), state.get('reason', 'unknown'))}）\n"
        f"當前狀態：位置 x={pos.get('x', '?'):.1f}, z={pos.get('z', '?'):.1f}，血量={health}/20，飢餓={food}/20\n"
        f"目標水面：{water}\n\n"
        f"周圍地形（B=Bot, W=水, .=可走, #=阻擋, ~=懸崖）：\n"
        f"{map_section}\n\n"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n"
    )


async def handle(state: dict, llm: LLMClient) -> dict | None:
    activity = state.get("activity_name", state.get("activity", "unknown"))
    reason = state.get("reason", "unknown")
    inventory = state.get("inventory", [])
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    y = round(pos.get("y", 0))
    missing = state.get("missing", [])
    needed_for = state.get("needed_for")
    suggested_actions = state.get("suggested_actions", [])
    detail = state.get("detail")
    missing_count = state.get("missing_count")
    plan_context = state.get("plan_context")

    if _looks_like_getfood_subflow(activity, reason, plan_context):
        shortcut = _build_getfood_replan_from_smelting(state, plan_context)
        if shortcut:
            print("[Skill/activity_stuck] smelting/no_input 發生在 getfood 子流程，直接改走補食物 replan")
            return shortcut

    if activity == "hunting" and reason == "no_animals" and plan_context:
        shortcut = _build_hunting_replan_no_animals(state, plan_context)
        if shortcut:
            print("[Skill/activity_stuck] hunting/no_animals，直接改走換區域或改釣魚的 replan")
            return shortcut

    if activity == "getfood" and reason == "no_raw_food" and plan_context and _recent_hunting_no_animals(state):
        shortcut = _build_getfood_replan_after_failed_hunt(state, plan_context)
        if shortcut:
            print("[Skill/activity_stuck] getfood/no_raw_food 且最近剛 hunting/no_animals，直接改走換區域或改釣魚的 replan")
            return shortcut

    # ── Deterministic shortcut: mining no_tools + can craft pickaxe ──────────
    if activity == "mining" and reason == "no_tools":
        caps = state.get("capabilities") or {}
        if caps.get("can_make_pickaxe") and not state.get("craft_issue_suspected"):
            pending_steps = (plan_context or {}).get("pending_steps", [])
            # current cmd is mine X N, still needs to run after crafting
            current_cmd = (plan_context or {}).get("current_cmd", "")
            new_cmds = ["craft stone_pickaxe"]
            if current_cmd:
                new_cmds.append(current_cmd)
            new_cmds.extend(pending_steps)
            print(f"[Skill/activity_stuck] mining no_tools + can_make_pickaxe → replan craft then retry: {new_cmds}")
            return [
                {"command": "chat", "text": "我有材料可以合成石鎬，合成後繼續挖礦"},
                {"action": "replan", "commands": new_cmds},
            ]

    # ── Deterministic shortcut: makechest failed but existing chest has free slots ──
    if activity == "makechest":
        chests = state.get("chests") or []
        usable = [c for c in chests if (c.get("freeSlots") or 0) > 0]
        if usable:
            chest = usable[0]
            pending_steps = (plan_context or {}).get("pending_steps", [])
            new_cmds = [f"deposit {chest['id']}"] + pending_steps
            print(f"[Skill/activity_stuck] makechest 失敗但有現有箱子 id={chest['id']}，直接 deposit")
            return [
                {"command": "chat", "text": f"改用已有箱子 id={chest['id']} 整理背包"},
                {"action": "replan", "commands": new_cmds},
            ]

    # ── Deterministic shortcut: getfood has_raw_food → replan smelt + getfood ──
    if activity == "getfood" and reason == "has_raw_food":
        raw_food = state.get("raw_food")
        raw_count = state.get("raw_count", 1)
        remaining_food = state.get("remaining", raw_count)
        pending_steps = (plan_context or {}).get("pending_steps", [])
        after_smelt = max(0, remaining_food - raw_count)
        new_cmds = [f"smelt {raw_food} {raw_count}"]
        if after_smelt > 0:
            new_cmds.append(f"getfood count {after_smelt}")
        new_cmds.extend(pending_steps)
        print(f"[Skill/activity_stuck] getfood has_raw_food → deterministic replan: {new_cmds}")
        return [
            {"command": "chat", "text": f"冶煉 {raw_food} x{raw_count}，完成後繼續補充食物"},
            {"action": "replan", "commands": new_cmds},
        ]

    remaining = state.get("remaining")  # getfood no_raw_food: 還需幾個熟食
    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory) or "（空背包）"
    reason_desc = REASON_DESC.get(reason, reason)
    extra_lines = []
    if missing:
        extra_lines.append(f"缺少資源/工具：{', '.join(missing)}")
    if needed_for:
        extra_lines.append(f"用途：{needed_for}")
    if suggested_actions:
        extra_lines.append(f"可考慮動作：{', '.join(suggested_actions)}")
    if missing_count is not None:
        extra_lines.append(f"缺少數量：{missing_count}")
    if state.get("craft_issue_suspected"):
        extra_lines.append("注意：目前看起來不是單純缺資源，而是 craft 流程可能異常失敗")
    if detail:
        extra_lines.append(f"補充說明：{detail}")
    extra = "\n".join(extra_lines)

    if activity == "fishing":
        prompt = _build_fishing_prompt(state, health, food)
    else:
        plan_section = ""
        pending_steps = []
        if plan_context:
            done = ', '.join(plan_context.get('done_steps', [])) or '（無）'
            pending_steps = plan_context.get('pending_steps', [])
            pending = ', '.join(pending_steps) or '（無）'
            plan_section = (
                f"\n【計畫進度】目標：{plan_context.get('goal', '?')}\n"
                f"共 {plan_context.get('total_steps', '?')} 步，"
                f"當前第 {plan_context.get('current_step', 0) + 1} 步：{plan_context.get('current_cmd', '?')}\n"
                f"已完成：{done}\n"
                f"待執行：{pending}\n"
            )
        remaining_note = f"\n還需熟食數量：{remaining} 個（請用此數字作為 getfood count）\n" if remaining is not None else ""
        pending_note = (
            f"\n原計畫剩餘步驟（replan 時必須附加在 hunt+getfood 之後）：{pending_steps}\n"
            if pending_steps else ""
        )
        chests_section = ""
        if activity == "makechest":
            chests = state.get("chests") or []
            if chests:
                chests_lines = "\n".join(
                    f"- id={c['id']} label={c.get('label','未分類')} freeSlots={c.get('freeSlots','?')}"
                    for c in chests
                )
                chests_section = f"\n已登記箱子：\n{chests_lines}\n"
            else:
                chests_section = "\n已登記箱子：（無）\n"

        prompt = (
            f"機器人在執行「{activity}」時中斷（原因：{reason_desc}）\n"
            f"當前狀態：位置 Y={y}，血量={health}/20，飢餓={food}/20\n\n"
            f"背包內容：\n{inv_summary}\n\n"
            f"{extra}"
            f"{chests_section}"
            f"{remaining_note}"
            f"{pending_note}"
            f"{plan_section}\n"
            f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
            f"請決定機器人接下來要做什麼。"
        )

    system = SYSTEM_PROMPTS.get(activity, SYSTEM_PROMPT_FALLBACK)
    if plan_context:
        system = system + PLAN_CONTEXT_SUFFIX

    response = None
    try:
        print(f"[Skill/activity_stuck] Prompt:\n{prompt}\n---")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=system,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = _parse_json_with_repair(clean)

        if _should_prefer_replan(activity, reason, plan_context) and decision.get("action") not in {"replan", "skip"}:
            repaired = await _reprompt_for_replan_strategy(
                llm,
                prompt,
                system,
                decision,
                pending_steps,
            )
            if not repaired:
                return _replan_fallback("我需要先重新規劃剩餘步驟，這次先跳過目前卡住的修復。")
            decision = repaired

        if decision.get("action") == "replan" and decision.get("commands"):
            errors = validate_commands(
                decision.get("commands", []),
                allowed_commands=PLAN_ALLOWED_COMMANDS,
            )
            if errors:
                repaired = await _reprompt_invalid_replan(
                    llm,
                    prompt,
                    system,
                    invalid_commands=[error.command for error in errors],
                    errors=errors,
                )
                if not repaired:
                    return _replan_fallback("我剛剛重新規劃失敗，先跳過這一步繼續。")
                decision = repaired
            result = []
            if decision.get("text"):
                result.append({"command": "chat", "text": decision["text"]})
            result.append({"action": "replan", "commands": decision["commands"]})
            return result
        if decision.get("action") == "skip":
            result = []
            if decision.get("text"):
                result.append({"command": "chat", "text": decision["text"]})
            result.append({"action": "skip"})
            return result

        decision = _normalize_decision(activity, reason, needed_for, missing, missing_count, decision)
        if not _is_valid_decision(decision):
            print(f"[Skill/activity_stuck] 無效 decision，忽略: {decision}")
            return None
        text = decision.get("text", "").strip()
        command = decision.get("command")
        result = []
        if command == "chat":
            if text:
                result.append({"command": "chat", "text": text})
            return result or None
        if text:
            result.append({"command": "chat", "text": text})
        if command != "idle":
            cmd = {k: v for k, v in decision.items() if k != "text"}
            result.append(cmd)
        return result if result else None
    except Exception as e:
        print(f"[Skill/activity_stuck] 解析失敗: {e}\n原始回應: {response!r}")
        if plan_context:
            return _replan_fallback("我剛剛重新規劃失敗，先跳過這一步繼續。")
        return None
