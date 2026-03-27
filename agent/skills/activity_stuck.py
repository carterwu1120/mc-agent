import json
import re
from agent.brain import LLMClient

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
機器人因背包沒有可燒製的原料而中斷，請根據當前資源決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "mine", "args": ["iron"], "text": "...理由..."}
{"command": "mine", "args": ["diamond"], "text": "...理由..."}
{"command": "chop", "text": "...理由..."}
{"command": "chat", "text": "...提醒內容..."}
{"command": "idle", "text": "...理由..."}

決策原則：
- 若背包有 iron_ingot >= 3 但沒有 iron_pickaxe → 用 chat 提醒玩家可以合成鐵鎬
- 若背包有 iron_ingot 足夠工具已齊全 → mine diamond
- 若背包沒有礦石也沒有木材燃料 → mine iron 補充資源
- 若沒有明確需求 → idle
- 禁止回覆 fish、smelt
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
    'timeout':   '操作超時',
}


async def handle(state: dict, llm: LLMClient) -> dict | None:
    activity = state.get("activity_name", state.get("activity", "unknown"))
    reason = state.get("reason", "unknown")
    inventory = state.get("inventory", [])
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    y = round(pos.get("y", 0))

    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory) or "（空背包）"
    reason_desc = REASON_DESC.get(reason, reason)

    prompt = (
        f"機器人在執行「{activity}」時中斷（原因：{reason_desc}）\n"
        f"當前狀態：位置 Y={y}，血量={health}/20，飢餓={food}/20\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"請決定機器人接下來要做什麼。"
    )

    response = None
    try:
        print(f"[Skill/activity_stuck] Prompt:\n{prompt}\n---")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPTS.get(activity, SYSTEM_PROMPT_FALLBACK),
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = json.loads(clean)
        result = []
        text = decision.get("text", "").strip()
        if text:
            result.append({"command": "chat", "text": text})
        if decision.get("command") != "idle":
            cmd = {k: v for k, v in decision.items() if k != "text"}
            result.append(cmd)
        return result if result else None
    except Exception as e:
        print(f"[Skill/activity_stuck] 解析失敗: {e}\n原始回應: {response!r}")
        return None
