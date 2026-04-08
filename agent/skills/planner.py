import asyncio
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
from agent import task_memory
from agent.plan_utils import normalize_commands

_PLANNER_ALLOWED_KEYS = [
    "mine", "chop", "fish", "smelt", "combat",
    "stopmine", "stopchop", "stopfish", "stopsmelt", "stopcombat", "stopsurface", "stopexplore",
    "home", "back", "surface", "explore",
    "deposit", "withdraw", "makechest", "labelchest", "equip", "come",
]
_PLANNER_COMMANDS = command_list(_PLANNER_ALLOWED_KEYS)

SYSTEM_PROMPT = f"""你是 Minecraft 機器人的任務規劃助手。
玩家用自然語言下達指令，你要轉換成機器人可執行的指令序列。
只能回覆以下其中一種 JSON（不含其他文字）：
{{"action": "plan", "goal": "簡短描述玩家目標", "commands": ["chop logs 20", "mine iron 10"]}}
{{"action": "chat", "text": "我聽不懂你的意思"}}

【可用指令與格式】
{_PLANNER_COMMANDS}
- come [player]  走向玩家；若玩家叫你「過來 / come here / 來我這」，優先用這個

【規則】
- 只能使用「可用指令」清單中的指令，嚴禁發明清單以外的指令
- 若玩家要求的事情無法用清單指令完成（例如設定天氣、給予物品、傳送玩家、執行伺服器指令等），回傳 chat 說明無法執行
- 多個活動依序排入 commands 陣列，長度不限，根據實際需求決定
- 若當前有活動進行中（activity != idle），先加入對應 stop 指令再排新活動
- 若玩家只是在說停止、停下、先停、stop，優先規劃停止當前活動；若目前沒有活動就回 chat
- 若玩家明確要求你靠近他、過去找他、跟上他，規劃 come 指令；若知道玩家名稱就用 come <player>
- 若玩家要求你回到地面、地表、陸地、上去，優先規劃 surface
- 玩家沒說數量時才用合理預設值（木頭 32，釣魚 20）；若是前置條件修復（例如缺工具、缺材料），請優先根據「缺多少補多少」來決定數量，不要一律固定用 16
- smelt 指令必須帶數量，不可省略，否則會把所有原料全部放入熔爐
- 玩家問問題、打招呼、或說的不是任務指令時，回傳 chat
- 只輸出 JSON，不要加任何解釋或其他文字

【前置條件推理（重要）】
收到模糊或複合目標時，根據背包狀態（inventory）和裝備狀態（capabilities）自動推斷前置步驟，依序加入 commands：

裝備類：
- 若目標需要挖掘或戰鬥，先確認工具；若缺對應工具 → 先 equip
  ⚠️ equip 是裝備現有工具，不是「去打架」。combat 不是前置步驟，絕對不要把 combat 加進 commands 作為準備步驟
  ⚠️ 不要把 equip 當成通用銜接步驟；只有在「前一步剛產生可裝備的新工具/武器/盔甲」或「明確看到目前裝備不足」時才加入
  ⚠️ 若 plan 中已經有一個 equip，而且中間沒有會新增裝備的步驟，不要再重複插入 equip
- 若食物不足（cooked_total < 5）：
  根據主目標決定目標熟食數量（food_target）：
  - 短暫任務（砍樹、存物品、來回跑腿）→ food_target = 8
  - 一般任務（挖鐵、挖石）→ food_target = 16
  - 長時間/危險任務（挖鑽石、下地深挖、combat、長途探索）→ food_target = 32
  取得方式：
  - 背包有生食（raw_total > 0）→ 先 getfood count <food_target>（自動冶煉）
  - 背包無生食 → 先 hunt count <food_target>，再 getfood count <food_target>
    （不要假設每隻動物會掉 2 個原料；hunt count 與熟食目標先採 1:1 的保守估計）
  - 不要使用 fish 作為前置步驟，除非玩家明確要求釣魚

工具鏈：
- 挖鑽石 → 需要鐵鎬（iron_pickaxe）→ 需要鐵錠 → 若背包無鐵錠：先補足做鐵鎬所需的鐵（通常 3 個 iron_ingot），再 mine diamond
- 挖金/鐵 → 需要石鎬（stone_pickaxe）→ 若無石頭工具：先補足做石鎬所需的 stone/cobblestone（通常 3 個），再 mine iron/gold
- 冶煉 → 需要燃料：**優先用木頭/木板**（oak_log, planks 等均可）→ 只有在背包無任何木頭且無煤炭時，才 mine coal；絕對不要在背包有木頭的情況下加 mine coal
- 製作箱子 → 需要木材 → 若木材不足：先 chop → 再 makechest
- 若只是為了補工具鏈，數量要保守精算：
  - 補 stone_pickaxe：mine stone 3（或略多一點 buffer）
  - 補 iron_pickaxe：mine iron 3 → smelt raw_iron 3 → equip
  - 不要產生 chop → equip → smelt、或 equip → equip 這類沒有實際意義的序列
  - 不要在缺口明確時一律回 mine iron 16 / smelt raw_iron 16

範例：
玩家說「幫我準備去挖鑽石」，背包無鐵鎬、食物只剩 2：
→ ["hunt count 32", "getfood count 32", "mine iron 3", "smelt raw_iron 3", "equip", "mine diamond 10"]
  （挖鑽石是長時間危險任務 → food_target=32；若只是為了先補鐵鎬，鐵的前置數量應接近實際缺口）

玩家說「我要釣魚」，背包無釣竿、有木材：
→ ["fish catches 20"]  ← bot 會自動製作釣竿，不需要手動加步驟

只在背包狀態確實缺少時才加前置步驟，不要過度規劃。

【箱子相關流程】
- 若已有對應類別的箱子（chests 資訊中有 label 且 freeSlots > 0）→ 直接 deposit <id>
- 若沒有對應箱子，但需要整理物品 → makechest 後接 labelchest + deposit：
  ["makechest", "labelchest {{new_chest_id}} <label>", "deposit {{new_chest_id}}"]
  {{new_chest_id}} 是佔位符，makechest 完成後自動填入，不要替換成數字
  label 根據要存入的物品類型：wood / ore / stone / misc / food
"""

RESUME_PATTERNS = [
    r"^\s*繼續\s*$",
    r"^\s*continue\s*$",
    r"^\s*resume\s*$",
    r"^\s*resumetask\s*$",
    r"^\s*繼續任務\s*$",
    r"^\s*繼續上次的\s*$",
]

COME_PATTERNS = [
    r"\bcome here\b",
    r"\bcome to me\b",
    r"過來",
    r"來我這",
    r"來我這裡",
    r"來找我",
    r"跟我來",
    r"跟上",
]

SURFACE_PATTERNS = [
    r"\bsurface\b",
    r"\bgo to surface\b",
    r"\bgo above ground\b",
    r"回到地面",
    r"回地表",
    r"到地面",
    r"到地表",
    r"上去",
    r"回到陸地",
]

STOP_PATTERNS = [
    r"^\s*stop\s*$",
    r"^\s*停止\s*$",
    r"^\s*停下(來)?\s*$",
    r"^\s*先停(下來)?\s*$",
    r"^\s*不要做了\s*$",
]

_TRANSIENT_LLM_ERROR_PATTERNS = (
    "503",
    "unavailable",
    "high demand",
    "try again later",
)


def _is_transient_llm_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(pattern in text for pattern in _TRANSIENT_LLM_ERROR_PATTERNS)


async def _chat_with_retry(llm: LLMClient, prompt: str, system: str, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await llm.chat(
                [{"role": "user", "content": prompt}],
                system=system,
            )
        except Exception as e:
            last_error = e
            if not _is_transient_llm_error(e) or attempt == attempts:
                raise
            delay = attempt
            print(f"[Planner] LLM 暫時不可用，第 {attempt}/{attempts} 次失敗，{delay}s 後重試: {e}")
            await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error


def _parse_decision_text(response: str) -> dict:
    clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
    clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
    return json.loads(clean)


def _planner_failure_chat() -> dict:
    return {"command": "chat", "text": "我這次規劃失敗了，請再說一次。"}


async def _reprompt_invalid_plan(
    llm: LLMClient,
    prompt: str,
    invalid_commands: list[str],
    errors,
) -> dict | None:
    reprompt = prompt + build_reprompt_suffix(
        invalid_commands=invalid_commands,
        errors=errors,
        allowed_command_keys=_PLANNER_ALLOWED_KEYS,
    )
    try:
        print(f"[Planner] 偵測到非法計畫，重問一次 LLM：{invalid_commands}")
        corrected = await _chat_with_retry(llm, reprompt, SYSTEM_PROMPT)
        decision = _parse_decision_text(corrected)
        if decision.get("action") != "plan":
            return None
        correction_errors = validate_commands(
            decision.get("commands", []),
            allowed_commands=PLAN_ALLOWED_COMMANDS,
        )
        if correction_errors:
            print(f"[Planner] 修正後計畫仍不合法：{[e.command for e in correction_errors]}")
            return None
        print(f"[Planner] 修正後計畫: {decision.get('commands')}")
        return decision
    except Exception as e:
        print(f"[Planner] 修正計畫失敗: {e}")
        return None


def _stop_command_for_activity(activity: str) -> str | None:
    stop_map = {
        "fishing": "stopfish",
        "chopping": "stopchop",
        "mining": "stopmine",
        "smelting": "stopsmelt",
        "surface": "stopsurface",
        "explore": "stopexplore",
        "combat": "stopcombat",
        "hunting": "stophunt",
        "getfood": "stopgetfood",
    }
    return stop_map.get(activity)


def _maybe_plan_come(message: str, activity: str, player_name: str | None) -> dict | None:
    lowered = message.lower()
    if not any(re.search(pattern, lowered if pattern.startswith(r"\b") else message) for pattern in COME_PATTERNS):
        return None

    commands: list[str] = []
    stop_map = {
        "fishing": "stopfish",
        "chopping": "stopchop",
        "mining": "stopmine",
        "smelting": "stopsmelt",
        "surface": "stopsurface",
        "explore": "stopexplore",
        "combat": "stopcombat",
        "hunting": "stophunt",
        "getfood": "stopgetfood",
    }
    stop_cmd = stop_map.get(activity)
    if stop_cmd:
        commands.append(stop_cmd)

    if player_name:
        commands.append(f"come {player_name}")
    else:
        commands.append("come")

    return {"action": "plan", "commands": commands}


def _maybe_plan_surface(message: str, activity: str) -> dict | None:
    lowered = message.lower()
    if not any(re.search(pattern, lowered if pattern.startswith(r"\b") else message) for pattern in SURFACE_PATTERNS):
        return None

    commands: list[str] = []
    stop_map = {
        "fishing": "stopfish",
        "chopping": "stopchop",
        "mining": "stopmine",
        "smelting": "stopsmelt",
        "surface": "stopsurface",
        "explore": "stopexplore",
        "combat": "stopcombat",
        "hunting": "stophunt",
        "getfood": "stopgetfood",
    }
    stop_cmd = stop_map.get(activity)
    if stop_cmd:
        commands.append(stop_cmd)
    commands.append("surface")
    return {"action": "plan", "commands": commands}


def _maybe_plan_stop(message: str, activity: str) -> dict | None:
    if not any(re.search(pattern, message.lower() if pattern.startswith(r"^\s*stop") else message) for pattern in STOP_PATTERNS):
        return None
    stop_cmd = _stop_command_for_activity(activity)
    if not stop_cmd:
        return {"command": "chat", "text": "目前沒有正在進行的活動可停止。"}
    return {"action": "plan", "commands": [stop_cmd]}


async def handle(state: dict, llm: LLMClient) -> dict | None:
    message = state.get("message", "")
    player_name = state.get("from")
    activity = state.get("activity", "idle")
    mode = state.get("mode", "survival")
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    stack = state.get("stack", [])
    chests = state.get("chests", [])

    top = stack[-1] if stack else {}
    goal = top.get("goal", {})
    progress = top.get("progress", {})
    goal_str = f"目標：{goal}，進度：{progress}" if goal else "（無目標）"

    interrupted_task = task_memory.load()
    task_ctx = (
        f"（有未完成任務：{interrupted_task['goal']}，第 {interrupted_task['currentStep']} 步）"
        if interrupted_task and interrupted_task.get("status") == "interrupted"
        else ""
    )

    chests_summary = "\n".join(
        f"- id={c['id']} label={c.get('label','未分類')} freeSlots={c.get('freeSlots','?')} contents={[i['name'] for i in c.get('contents', [])]}"
        for c in chests
    ) or "（無已登記箱子）"

    prompt = (
        f"玩家說：「{message}」\n\n"
        f"機器人目前狀態：活動={activity}，模式={mode}，"
        f"位置=({pos.get('x',0):.0f}, {pos.get('y',0):.0f}, {pos.get('z',0):.0f})，"
        f"血量={health}/20，飢餓={food}/20。\n"
        f"當前任務：{goal_str}{task_ctx}\n\n"
        f"已登記箱子：\n{chests_summary}\n\n"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
        f"請根據玩家的話決定要做什麼。"
    )

    response = None
    try:
        print(f"[Planner] 玩家: {message}")

        # 繼續未完成任務
        if any(re.search(p, message, re.IGNORECASE) for p in RESUME_PATTERNS):
            task = task_memory.load()
            if task and task.get('status') == 'interrupted':
                steps = task.get("steps")
                if steps:
                    remaining_cmds = [s["cmd"] for s in steps if s["status"] not in ("done",)]
                else:
                    remaining_cmds = task['commands'][task['currentStep']:]
                if remaining_cmds:
                    # If the first resume command matches the current running activity,
                    # JS already auto-resumed it — skip that step to avoid double-push
                    first_cmd = remaining_cmds[0].split()[0]
                    current_activity = activity  # from state
                    activity_for_cmd = {
                        'chop': 'chopping', 'mine': 'mining', 'fish': 'fishing',
                        'smelt': 'smelting', 'hunt': 'hunting', 'getfood': 'getfood',
                    }.get(first_cmd)
                    if activity_for_cmd and current_activity == activity_for_cmd:
                        print(f"[Planner] 恢復任務: {task['goal']} — 已在執行 {current_activity}，跳過重複指令")
                        remaining_cmds = remaining_cmds[1:]
                    if not remaining_cmds:
                        return {"command": "chat", "text": "任務已在執行中。"}
                    done_steps = [s["cmd"] for s in (steps or []) if s["status"] == "done"]
                    print(f"[Planner] 恢復任務: {task['goal']} — 已完成: {done_steps}, 剩餘: {remaining_cmds}")
                    return {"action": "plan", "commands": remaining_cmds, "goal": task['goal']}
            return {"command": "chat", "text": "目前沒有未完成的任務可以繼續。"}

        shortcut = _maybe_plan_come(message, activity, player_name)
        if shortcut:
            print(f"[Planner] 快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_surface(message, activity)
        if shortcut:
            print(f"[Planner] 快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_stop(message, activity)
        if shortcut:
            print(f"[Planner] 快捷規劃: {shortcut.get('commands')}")
            return shortcut
        response = await _chat_with_retry(llm, prompt, SYSTEM_PROMPT)
        decision = _parse_decision_text(response)

        if decision.get("action") == "plan":
            commands = normalize_commands(decision.get("commands", []))
            errors = validate_commands(commands, allowed_commands=PLAN_ALLOWED_COMMANDS)
            if errors:
                repaired = await _reprompt_invalid_plan(
                    llm,
                    prompt,
                    invalid_commands=[error.command for error in errors],
                    errors=errors,
                )
                if repaired:
                    return repaired
                return _planner_failure_chat()
            decision["commands"] = commands
            print(f"[Planner] 計畫: {decision.get('commands')}")
            return decision  # agent.py routes to executor

        if decision.get("action") == "chat":
            return {"command": "chat", "text": decision.get("text", "")}

    except Exception as e:
        print(f"[Planner] 解析失敗: {e}\n原始回應: {response!r}")

    return _planner_failure_chat()
