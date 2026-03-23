import json
import re
from agent.brain import LLMClient

SYSTEM_PROMPTS = {
    "mining": """你是 Minecraft 機器人的挖礦後行動助手。
機器人剛完成挖礦，請根據當前資源決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "smelt", "args": ["iron"], "text": "...理由..."}
{"command": "smelt", "args": ["gold"], "text": "...理由..."}
{"command": "smelt", "args": ["copper"], "text": "...理由..."}
{"command": "chat", "text": "...提醒內容..."}
{"command": "idle", "text": "...理由..."}

決策原則：
- 若有 raw_iron / iron_ore / deepslate_iron_ore → 回覆 smelt iron
- 若有 raw_gold / gold_ore / deepslate_gold_ore → 回覆 smelt gold
- 若有 raw_copper / copper_ore / deepslate_copper_ore → 回覆 smelt copper
- 若背包有 iron_ingot >= 3 但沒有 iron_pickaxe → 用 chat 提醒玩家可以合成鐵鎬
- 若以上都沒有 → idle
- 禁止回覆 fish、chop、mine
""",

    "smelting": """你是 Minecraft 機器人的燒製後行動助手。
機器人剛完成燒製，請根據當前資源決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "mine", "args": ["diamond"], "text": "...理由..."}
{"command": "mine", "args": ["iron"], "text": "...理由..."}
{"command": "chat", "text": "...提醒內容..."}
{"command": "idle", "text": "...理由..."}

決策原則：
- 若背包有 iron_ingot >= 3 但沒有 iron_pickaxe → 用 chat 提醒玩家可以合成鐵鎬
- 若背包有 diamond_ingot >= 3 但沒有 diamond_pickaxe → 用 chat 提醒玩家可以合成鑽石鎬
- 若工具齊全 → 建議繼續挖礦（mine diamond 或 mine iron）
- 若沒有明確需求 → idle
- 禁止回覆 fish、chop、smelt
""",

    "fishing": """你是 Minecraft 機器人的釣魚後行動助手。
機器人剛完成釣魚。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下 JSON（不要加任何其他文字）：
{"command": "idle", "text": "...理由..."}

釣魚結束後一律回覆 idle。
""",

    "woodcutting": """你是 Minecraft 機器人的砍樹後行動助手。
機器人剛完成砍樹，請根據當前資源決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "smelt", "args": ["coal"], "text": "...理由..."}
{"command": "mine", "args": ["iron"], "text": "...理由..."}
{"command": "chat", "text": "...提醒內容..."}
{"command": "idle", "text": "...理由..."}

決策原則：
- 若有大量木材但沒有煤炭作燃料 → 可燒木材製炭（smelt coal）
- 若有足夠工具和木材 → 建議去挖礦（mine iron）
- 否則 → idle
- 禁止回覆 fish、chop
""",
}

SYSTEM_PROMPT_FALLBACK = """你是 Minecraft 機器人的行動規劃助手。
機器人剛完成一項活動，請根據當前資源和狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"command": "chat", "text": "...說明..."}
{"command": "idle", "text": "...理由..."}

若沒有明確下一步，回覆 idle。
"""

SMELTABLE = {
    'raw_iron': 'iron_ingot', 'iron_ore': 'iron_ingot', 'deepslate_iron_ore': 'iron_ingot',
    'raw_gold': 'gold_ingot', 'gold_ore': 'gold_ingot', 'deepslate_gold_ore': 'gold_ingot',
    'raw_copper': 'copper_ingot', 'copper_ore': 'copper_ingot', 'deepslate_copper_ore': 'copper_ingot',
    'sand': 'glass', 'cobblestone': 'stone',
}


async def handle(state: dict, llm: LLMClient) -> dict | None:
    inventory = state.get("inventory", [])
    activity = state.get("activity_name", state.get("activity", "unknown"))
    reason = state.get("reason", "unknown")
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    y = round(pos.get("y", 0))

    inv_map = {i['name']: i['count'] for i in inventory}
    smeltable_lines = [
        f"- {name} x{inv_map[name]} → {out}"
        for name, out in SMELTABLE.items() if name in inv_map
    ]
    smeltable_section = (
        "可燒製的原料：\n" + "\n".join(smeltable_lines)
        if smeltable_lines else "（無可燒製原料）"
    )

    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory) or "（空背包）"

    prompt = (
        f"機器人剛完成活動：{activity}（原因：{reason}）\n"
        f"當前狀態：位置 Y={y}，血量={health}/20，飢餓={food}/20\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"{smeltable_section}\n\n"
        f"請決定機器人下一步要做什麼。"
    )

    response = None
    try:
        print(f"[Skill/activity_done] Prompt:\n{prompt}\n---")
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
        print(f"[Skill/activity_done] 解析失敗: {e}\n原始回應: {response!r}")
        return None
