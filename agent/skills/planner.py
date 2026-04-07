import json
import re
from agent.brain import LLMClient
from agent.skills.state_summary import summary_json
from agent.skills.commands_ref import command_list
from agent import task_memory

_PLANNER_COMMANDS = command_list([
    "mine", "chop", "fish", "smelt", "combat",
    "stopmine", "stopchop", "stopfish", "stopsmelt", "stopcombat", "stopsurface", "stopexplore",
    "home", "back", "surface", "explore",
    "deposit", "withdraw", "makechest", "labelchest", "equip", "come",
])

SYSTEM_PROMPT = f"""你是 Minecraft 機器人的任務規劃助手。
玩家用自然語言下達指令，你要轉換成機器人可執行的指令序列。
只能回覆以下其中一種 JSON（不含其他文字）：
{{"action": "plan", "goal": "簡短描述玩家目標", "commands": ["chop logs 20", "mine iron 10"]}}
{{"action": "chat", "text": "我聽不懂你的意思"}}

【可用指令與格式】
{_PLANNER_COMMANDS}
- come [player]  走向玩家；若玩家叫你「過來 / come here / 來我這」，優先用這個

【規則】
- 多個活動依序排入 commands 陣列
- 若當前有活動進行中（activity != idle），先加入對應 stop 指令再排新活動
- 若玩家只是在說停止、停下、先停、stop，優先規劃停止當前活動；若目前沒有活動就回 chat
- 若玩家明確要求你靠近他、過去找他、跟上他，規劃 come 指令；若知道玩家名稱就用 come <player>
- 若玩家要求你回到地面、地表、陸地、上去，優先規劃 surface
- 玩家沒說數量時用合理預設值（木頭 32，礦石 16，釣魚 20，冶煉依玩家需求數量）
- smelt 指令必須帶數量，不可省略，否則會把所有原料全部放入熔爐
- 玩家問問題、打招呼、或說的不是任務指令時，回傳 chat
- 只輸出 JSON，不要加任何解釋或其他文字

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

    prompt = (
        f"玩家說：「{message}」\n\n"
        f"機器人目前狀態：活動={activity}，模式={mode}，"
        f"位置=({pos.get('x',0):.0f}, {pos.get('y',0):.0f}, {pos.get('z',0):.0f})，"
        f"血量={health}/20，飢餓={food}/20。\n"
        f"當前任務：{goal_str}{task_ctx}\n\n"
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
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = json.loads(clean)

        if decision.get("action") == "plan":
            print(f"[Planner] 計畫: {decision.get('commands')}")
            return decision  # agent.py routes to executor

        if decision.get("action") == "chat":
            return {"command": "chat", "text": decision.get("text", "")}

    except Exception as e:
        print(f"[Planner] 解析失敗: {e}\n原始回應: {response!r}")

    return None
