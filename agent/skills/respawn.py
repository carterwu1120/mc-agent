import json
import re
from agent.brain import LLMClient

SYSTEM_PROMPT = """你是 Minecraft 機器人的死亡復活處理助手。
機器人剛剛死亡並重生，請根據死因、剩餘任務與當前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：

{"action": "plan", "commands": ["surface", "mine iron 10"], "text": "...理由..."}
{"action": "plan", "commands": ["tp 13 5 105", "mine iron 10"], "text": "...理由..."}
{"command": "chat", "text": "...告知玩家無法繼續的原因..."}
{"command": "idle", "text": "...理由..."}

【可用前置指令】
- tp <x> <y> <z>   傳送回原本工作位置（只有死因是 other 且位置安全時才用）
- surface          先爬到地面再繼續（死在地底、岩漿、溺水時優先考慮）
- home             回基地（任務需要重新準備時）
- equip            重新裝備（死後可能裝備散落）

【決策原則】
- 死因 lava 或 drowning → 不要 tp 回原位，優先 surface 再繼續任務
- 死因 other（被怪打、摔死）且 startPos 有效 → 可考慮 tp 回去繼續
- 若剩餘任務已無意義（例如任務需要的位置明顯危險）→ chat 告知玩家
- 若沒有未完成任務 → idle
- commands 陣列中，前置指令（tp/surface/home/equip）之後接剩餘任務指令
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

    prompt = (
        f"機器人剛死亡重生。\n"
        f"死亡原因：{cause}\n"
        f"死前工作位置（startPos）：{start_pos_str}\n"
        f"重生位置：{spawn_pos_str}\n"
        f"未完成任務目標：{goal}\n"
        f"剩餘任務指令：{remaining}\n\n"
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
