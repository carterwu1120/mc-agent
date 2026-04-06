import json
import re
from agent.brain import LLMClient
from agent.skills.state_summary import equipment_summary
from agent.skills.commands_ref import command_list

_RESPAWN_COMMANDS = command_list(["tp", "equip", "home", "chat", "idle"])

SYSTEM_PROMPT = f"""你是 Minecraft 機器人的死亡復活處理助手。
機器人剛剛死亡並重生，請根據死因、剩餘任務與當前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：

{{"action": "plan", "commands": ["mine iron 10"], "text": "...理由..."}}
{{"action": "plan", "commands": ["tp 13 5 105", "mine iron 10"], "text": "...理由..."}}
{{"action": "plan", "commands": ["equip", "mine iron 10"], "text": "...理由..."}}
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
- 死因 other 且 startPos 有效 → 考慮先 tp 回死亡地點（撿裝備或繼續任務）
- 死因 lava 或 drowning → 不要 tp 回原位（危險），直接從重生點繼續
- 若任務明確無法繼續（例如工具全燒光且無法補充）→ chat 告知玩家
- 優先想辦法繼續任務，只有真的無法繼續才選 idle 或 chat
- commands 中只放實際需要的前置指令，不要多餘的步驟
- 禁止憑空捏造不在剩餘任務中的活動指令
"""


async def handle(state: dict, llm: LLMClient) -> list | dict | None:
    cause = state.get('cause', 'other')
    start_pos = state.get('startPos')
    spawn_pos = state.get('spawnPos') or state.get('pos') or {}
    remaining = state.get('remaining', [])
    goal = state.get('goal', '')
    inventory = state.get('inventory', [])
    health = state.get('health', 20)
    food = state.get('food', 20)

    if not remaining:
        return None

    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory) or "（空背包）"
    start_pos_str = f"({start_pos['x']:.0f}, {start_pos['y']:.0f}, {start_pos['z']:.0f})" if start_pos else "（無）"
    spawn_pos_str = f"({spawn_pos.get('x', 0):.0f}, {spawn_pos.get('y', 0):.0f}, {spawn_pos.get('z', 0):.0f})"

    equip_summary = equipment_summary(state)

    prompt = (
        f"機器人剛死亡重生。\n"
        f"死亡原因：{cause}\n"
        f"死前工作位置（startPos）：{start_pos_str}\n"
        f"重生位置：{spawn_pos_str}\n"
        f"未完成任務目標：{goal}\n"
        f"剩餘任務指令：{remaining}\n\n"
        f"目前裝備：\n{equip_summary}\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"血量={health}/20，飢餓={food}/20\n\n"
        f"請決定機器人重生後要怎麼繼續。"
    )

    response = None
    try:
        print(f"[Respawn] 死因={cause}，startPos={start_pos_str}，剩餘={remaining}")
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
            if commands:
                result.append({"action": "plan", "commands": commands, "goal": goal})
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
