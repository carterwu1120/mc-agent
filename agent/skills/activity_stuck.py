import json
import re
from agent.brain import LLMClient
from agent.skills.state_summary import summary_json

ALLOWED_COMMANDS = {
    "chop", "mine", "chat", "idle", "home", "back", "surface", "explore", "withdraw", "fishing_decision"
}

PLAN_CONTEXT_SUFFIX = """
【當前執行計畫】處於多步驟計畫執行中。除了常規決策外，也可以回傳重新規劃：
{"action": "replan", "commands": ["new step 1", "new step 2"], "text": "...理由..."}
commands 陣列替換目前未完成的所有步驟（包含當前失敗的步驟）。
只有在單步恢復無法解決問題時才使用 replan。
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
    "mining": """你是 Minecraft 機器人的挖礦卡住處理助手。
機器人在挖礦時遇到障礙而中斷，請根據當前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "chop", "text": "...理由..."}
{"command": "mine", "args": ["iron"], "text": "...理由..."}
{"command": "chat", "text": "...需要玩家幫助的說明..."}
{"command": "idle", "text": "...理由..."}

決策原則：
- 若原因為「無稿子且無法合成」→ 背包缺木材就先去砍樹（chop），有木材但缺鐵就先挖鐵礦（mine iron）
- 若原因為「四個方向都被基岩或不可挖方塊阻擋，機器人可能被困住」→ 用 chat 告知玩家機器人被困，請玩家用 /tp 解救
- 其他情況 → idle
""",

    "smelting": """你是 Minecraft 機器人的燒製卡住處理助手。
機器人在燒製過程中遇到依賴不足或材料問題而中斷，請根據當前資源決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "mine", "args": ["iron", "8"], "text": "...理由..."}
{"command": "mine", "args": ["diamond", "5"], "text": "...理由..."}
{"command": "mine", "args": ["stone", "8"], "text": "...理由..."}
{"command": "chop", "goal": {"logs": 4}, "text": "...理由..."}
{"command": "home", "text": "...理由..."}
{"command": "withdraw", "args": ["oak_log", "16", "1"], "text": "...理由..."}
{"command": "chat", "text": "...提醒內容..."}
{"command": "idle", "text": "...理由..."}

決策原則：
- 若 reason 是 missing_dependency，優先根據 missing / needed_for / suggested_actions 決定下一步
- 若選擇 mine，必須同時決定要挖的 target 與 count，不能只回 mine iron 這種沒有數量的指令
- 若 missing 包含 wood 或 crafting_material，優先 chop；若選擇 chop，必須同時決定 goal.logs 數量
- goal.logs 應根據缺口估算：只缺做稿子的基本材料時通常 3 到 5 根即可；若還要順便補工作台或備料，可給更高數量
- 若有 home 或 chest 資源線索，也可選 home / withdraw
- 若 missing 是 cobblestone，優先回 mine stone N 來補足熔爐材料，不要回 mine iron
- 當 missing_count 有提供時，mine 的 count 應至少等於 missing_count；例如缺 8 個 cobblestone，應回 mine stone 8 或更多
- 若背包有 iron_ingot >= 3 但沒有 iron_pickaxe → 用 chat 提醒玩家可以合成鐵鎬
- 若背包有 iron_ingot 足夠工具已齊全 → mine diamond
- 若背包沒有礦石也沒有木材燃料 → mine iron 補充資源
- 若沒有明確需求 → idle
- 禁止回覆 fish、smelt
""",

    "chopping": """你是 Minecraft 機器人的砍樹卡住處理助手。
機器人在砍樹時附近找不到可砍的樹，請根據目前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "back", "text": "...理由..."}
{"command": "surface", "text": "...理由..."}
{"command": "explore", "args": ["trees"], "text": "...理由..."}
{"command": "home", "text": "...理由..."}
{"command": "chat", "text": "...提醒內容..."}
{"command": "idle", "text": "...理由..."}

決策原則：
- 若目前明顯在地底或附近沒有樹，但這次任務仍是砍樹，優先用 surface；若不確定 surface 是否可行，再用 back 回到先前位置
- 若已經在地表但附近沒有樹，優先回覆 explore trees，移動到新的地表區域繼續砍樹任務
- 若已設定 home 且判斷回基地更合理，可用 home
- 若沒有明確安全的下一步，才用 chat 或 idle
- 不要回 chop；目前 chopping activity 已經卡住，先脫離目前位置再說
- 不要只因為現在是夜晚、白天、天色變化，就選擇 home、idle 或放棄任務
- 只有在 prompt 中有明確危險證據（例如 danger_score 很高、附近 hostile、血量/飢餓危險）時，才可以把安全性當成主要理由
""",

    "surface": """你是 Minecraft 機器人的回到地表卡住處理助手。
機器人在前往地表時因路徑或地形問題中斷，請根據目前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "back", "text": "...理由..."}
{"command": "home", "text": "...理由..."}
{"command": "chat", "text": "...提醒內容..."}
{"command": "idle", "text": "...理由..."}

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
    'no_tools':  '無稿子且無法合成（缺少木材或燃料）',
    'no_input':  '背包中沒有可燒製的原料',
    'no_fuel':   '沒有可用的燃料',
    'missing_dependency': '缺少執行目前活動所需的前置資源或工具',
    'cannot_cook_food': '有生食但目前無法完成烹飪流程',
    'bad_cast': '拋竿角度或站位不佳，無法正常落水',
    'no_bobber': '拋竿後持續找不到浮標，可能站位或拋竿位置異常',
    'no_trees': '附近找不到可砍的樹，可能目前位置不適合進行砍樹',
    'timeout':   '操作超時',
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
    if detail:
        extra_lines.append(f"補充說明：{detail}")
    extra = "\n".join(extra_lines)

    if activity == "fishing":
        prompt = _build_fishing_prompt(state, health, food)
    else:
        plan_section = ""
        if plan_context:
            done = ', '.join(plan_context.get('done_steps', [])) or '（無）'
            pending = ', '.join(plan_context.get('pending_steps', [])) or '（無）'
            plan_section = (
                f"\n【計畫進度】目標：{plan_context.get('goal', '?')}\n"
                f"共 {plan_context.get('total_steps', '?')} 步，"
                f"當前第 {plan_context.get('current_step', 0) + 1} 步：{plan_context.get('current_cmd', '?')}\n"
                f"已完成：{done}\n"
                f"待執行：{pending}\n"
            )
        prompt = (
            f"機器人在執行「{activity}」時中斷（原因：{reason_desc}）\n"
            f"當前狀態：位置 Y={y}，血量={health}/20，飢餓={food}/20\n\n"
            f"背包內容：\n{inv_summary}\n\n"
            f"{extra}"
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

        # Replan response — pass through directly without command validation
        if decision.get("action") == "replan" and decision.get("commands"):
            result = []
            if decision.get("text"):
                result.append({"command": "chat", "text": decision["text"]})
            result.append({"action": "replan", "commands": decision["commands"]})
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
        return None
