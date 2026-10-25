import json
import re
from agent import task_memory
from agent.brain import LLMClient
from agent.context_builder import build_for_skill
from agent.plan_utils import normalize_commands
from agent.skills.command_validation import PLAN_ALLOWED_COMMANDS, validate_commands
from agent.skills.llm_response import parse_llm_json
from agent.skills.state_summary import equipment_summary, summary_json
from agent.skills.commands_ref import command_list
from agent.skills.knowledge import knowledge

_RESPAWN_COMMANDS = command_list(sorted(PLAN_ALLOWED_COMMANDS), include_stops=False)

SYSTEM_PROMPT = f"""你是 Minecraft 機器人的死亡後恢復規劃者。
機器人剛重生。規劃恢復原任務前真正需要的前置步驟；commands 不要重寫主任務，也不要加入 resumetask。
只輸出以下其中一種 JSON，不要加其他文字：
{{"action":"plan","commands":[],"text":"可直接恢復的理由"}}
{{"action":"plan","commands":["..."],"text":"前置策略與理由"}}
{{"action":"wait","text":"目前無法安全恢復，以及需要玩家提供什麼"}}

{knowledge('respawn')}

{knowledge('bot_capabilities', '鎬子與工具鏈', '食物')}

可用前置指令：
{_RESPAWN_COMMANDS}
"""


async def handle(state: dict, llm: LLMClient) -> list | dict | None:
    cause = state.get('cause', 'other')
    start_pos = state.get('startPos')
    death_pos = state.get('deathPos')
    spawn_pos = state.get('spawnPos') or state.get('pos') or {}
    remaining = state.get('remaining', [])
    goal = state.get('goal', '')
    task_current_cmd = state.get('taskCurrentCmd')
    task_current_pos = state.get('taskCurrentPos')
    task_work_pos = state.get('taskWorkPos')
    task_goal = state.get('taskGoal') or {}
    task_progress = state.get('taskProgress') or {}
    task_activity = state.get('taskActivity')
    inventory = state.get('inventory', [])
    health = state.get('health', 20)
    food = state.get('food', 20)

    if not remaining:
        return None

    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory) or "（空背包）"
    start_pos_str = f"({start_pos['x']:.0f}, {start_pos['y']:.0f}, {start_pos['z']:.0f})" if start_pos else "（無）"
    death_pos_str = f"({death_pos['x']:.0f}, {death_pos['y']:.0f}, {death_pos['z']:.0f})" if death_pos else "（無）"
    spawn_pos_str = f"({spawn_pos.get('x', 0):.0f}, {spawn_pos.get('y', 0):.0f}, {spawn_pos.get('z', 0):.0f})"
    task_current_pos_str = f"({task_current_pos['x']:.0f}, {task_current_pos['y']:.0f}, {task_current_pos['z']:.0f})" if task_current_pos else "（無）"
    task_work_pos_str = f"({task_work_pos['x']:.0f}, {task_work_pos['y']:.0f}, {task_work_pos['z']:.0f})" if task_work_pos else "（無）"

    equip_summary = equipment_summary(state)

    prompt = (
        f"機器人剛死亡重生。\n"
        f"死亡原因：{cause}\n"
        f"死亡位置：{death_pos_str}\n"
        f"舊 activity startPos：{start_pos_str}\n"
        f"重生位置：{spawn_pos_str}\n"
        f"未完成任務目標：{goal}\n"
        f"目前中斷步驟：{task_current_cmd or '（無）'}\n"
        f"中斷步驟 activity：{task_activity or '（無）'}\n"
        f"中斷步驟當前位置（優先續接點）：{task_current_pos_str}\n"
        f"中斷步驟工作位置：{task_work_pos_str}\n"
        f"中斷步驟目標：{json.dumps(task_goal, ensure_ascii=False)}\n"
        f"中斷步驟進度：{json.dumps(task_progress, ensure_ascii=False)}\n"
        f"剩餘任務指令：{remaining}\n\n"
        f"目前裝備：\n{equip_summary}\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"血量={health}/20，飢餓={food}/20\n\n"
        f"完整決策狀態（JSON）：\n{summary_json(state)}\n"
        f"{build_for_skill('respawn', task_memory.recent_events(), task_memory.recent_failures(), task_memory.interrupted_tasks())}\n"
        f"請決定機器人重生後要怎麼繼續。"
    )

    response = None
    try:
        print(f"[Respawn] 死因={cause}，taskCurrentPos={task_current_pos_str}，workPos={task_work_pos_str}，剩餘={remaining}")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            reasoning_effort="medium",
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = parse_llm_json(json.loads(clean), "Respawn")

        text = decision.get("text", "").strip()
        result = []

        if decision.get("action") == "plan":
            commands = decision.get("commands", [])
            if not isinstance(commands, list) or len(commands) > 6:
                raise ValueError("respawn plan commands must be a list with at most 6 items")
            commands = normalize_commands(commands)
            if commands:
                errors = validate_commands(commands, allowed_commands=PLAN_ALLOWED_COMMANDS)
                if errors:
                    raise ValueError(str(errors))
            if text:
                result.append({"command": "chat", "text": text})
            recovery_commands = list(commands)
            recovery_commands.append("resumetask")
            result.append({"action": "plan", "commands": recovery_commands, "goal": "", "preserve_task": True})
            return result or None

        if decision.get("action") == "wait":
            return [{"command": "chat", "text": text}] if text else None

        raise ValueError("respawn decision must be plan or wait")

    except Exception as e:
        print(f"[Respawn] 解析失敗: {e}\n原始回應: {response!r}")

    return None
