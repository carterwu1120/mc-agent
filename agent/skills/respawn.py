import json
import re
from agent.brain import LLMClient
from agent.skills.state_summary import equipment_summary
from agent.skills.commands_ref import command_list

_RESPAWN_COMMANDS = command_list(["tp", "equip", "home", "chat", "idle"])

SYSTEM_PROMPT = f"""你是 Minecraft 機器人的死亡復活處理助手。
機器人剛剛死亡並重生，請根據死因、剩餘任務與當前狀態決定「恢復原任務前」需要做的前置動作。
主任務會在這些前置動作完成後自動從中斷步驟繼續，不需要你重寫剩餘任務。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只有在 deterministic 規則無法決定時，才回覆以下其中一種 JSON（不要加任何其他文字）：

{{"action": "plan", "commands": [], "text": "...理由..."}}
{{"action": "plan", "commands": ["tp 13 5 105"], "text": "...理由..."}}
{{"action": "plan", "commands": ["tp 13 5 105", "equip"], "text": "...理由..."}}
{{"action": "plan", "commands": ["equip"], "text": "...理由..."}}
{{"command": "chat", "text": "...告知玩家無法繼續的原因..."}}
{{"command": "idle", "text": "...理由..."}}

【重生機制說明】
- 機器人重生點一定在地表（床或世界出生點），不需要 surface 指令
- 重生後直接在地表，可立即繼續任務

【可選前置指令】（視情況加在任務指令之前）
{_RESPAWN_COMMANDS}

【裝備判斷規則】
- 死因 lava → 裝備可能被岩漿燒毀，檢查背包/身上是否有武器、工具、盔甲；若有缺損才加 equip
- 死因 other（被怪打、摔死）→ 通常有防噴裝，裝備掉在死亡地點；可考慮 tp 回去撿裝備再繼續
- 若 prompt 中顯示目前身上裝備齊全，不需要加 equip

【決策原則】
- 重生已在地表，優先直接繼續剩餘任務
- 若 prompt 中有「中斷步驟當前位置（優先續接點）」→ 這比舊 activity 的 startPos 更重要，優先以它作為 tp 目標
- 若沒有中斷步驟當前位置，才退回使用 workPos 或 startPos
- 死因 other 且有有效續接點 → 考慮先 tp 回該位置（撿裝備或繼續任務）
- 死因 lava 或 drowning → 不要 tp 回原位（危險），直接從重生點繼續
- 若任務明確無法繼續（例如工具全燒光且無法補充）→ chat 告知玩家
- 優先想辦法繼續任務，只有真的無法繼續才選 idle 或 chat
- commands 中只放實際需要的前置指令，不要多餘的步驟
- 不要把剩餘主任務指令再寫進 commands；系統會自動接回原本中斷的 task
- 禁止憑空捏造新的主活動指令
- 中斷步驟目標（taskGoal）比 lastGoal 更能反映目前真正要做的事
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

    def _fmt_pos(pos: dict | None) -> str:
        if not pos:
            return "（無）"
        return f"({pos['x']:.0f}, {pos['y']:.0f}, {pos['z']:.0f})"

    def _tp_cmd(pos: dict | None) -> str | None:
        if not pos:
            return None
        return f"tp {round(pos['x'])} {round(pos['y'])} {round(pos['z'])}"

    # Deterministic recovery priority:
    # other -> currentPos > workPos > startPos
    # dangerous deaths -> workPos > startPos (avoid returning to likely hazard point)
    dangerous_causes = {"lava", "drowning"}
    recovery_target = None
    recovery_label = ""
    if cause == "other":
        if task_current_pos:
            recovery_target = task_current_pos
            recovery_label = "中斷步驟當前位置"
        elif task_work_pos:
            recovery_target = task_work_pos
            recovery_label = "中斷步驟工作位置"
        elif start_pos:
            recovery_target = start_pos
            recovery_label = "舊 activity startPos"
    elif cause in dangerous_causes:
        if task_work_pos:
            recovery_target = task_work_pos
            recovery_label = "中斷步驟工作位置"
        elif start_pos:
            recovery_target = start_pos
            recovery_label = "舊 activity startPos"
    else:
        if task_work_pos:
            recovery_target = task_work_pos
            recovery_label = "中斷步驟工作位置"
        elif task_current_pos:
            recovery_target = task_current_pos
            recovery_label = "中斷步驟當前位置"
        elif start_pos:
            recovery_target = start_pos
            recovery_label = "舊 activity startPos"

    deterministic_commands = []
    tp_command = _tp_cmd(recovery_target)
    if tp_command:
        deterministic_commands.append(tp_command)
    if cause == "other":
        deterministic_commands.append("equip")

    if deterministic_commands:
        text = (
            f"死亡原因為 {cause}，先回到{recovery_label}{_fmt_pos(recovery_target)}"
            f"{'並重新裝備' if cause == 'other' else ''}，再接回原本任務。"
        )
        deterministic_commands.append("resumetask")
        print(f"[Respawn] deterministic recovery: {deterministic_commands}")
        return [
            {"command": "chat", "text": text},
            {"action": "plan", "commands": deterministic_commands, "goal": "", "preserve_task": True},
        ]

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
        f"請決定機器人重生後要怎麼繼續。"
    )

    response = None
    try:
        print(f"[Respawn] 死因={cause}，taskCurrentPos={task_current_pos_str}，workPos={task_work_pos_str}，剩餘={remaining}")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = json.loads(clean)

        text = decision.get("text", "").strip()
        result = []

        if decision.get("action") == "plan":
            commands = decision.get("commands", [])
            if text:
                result.append({"command": "chat", "text": text})
            recovery_commands = list(commands or [])
            recovery_commands.append("resumetask")
            result.append({"action": "plan", "commands": recovery_commands, "goal": "", "preserve_task": True})
            return result or None

        if decision.get("command") == "chat":
            return [{"command": "chat", "text": text}] if text else None

        if decision.get("command") == "idle":
            if text:
                result.append({"command": "chat", "text": text})
            return result or None

    except Exception as e:
        print(f"[Respawn] 解析失敗: {e}\n原始回應: {response!r}")

    return None
