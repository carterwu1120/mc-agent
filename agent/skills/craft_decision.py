import json
import re
from agent.brain import LLMClient

SYSTEM_PROMPT = """你是 Minecraft 機器人的合成決策助手。
機器人需要合成某樣物品，但有多種材料選項，請根據當前資源和目標做出最適合的決策。
只能回覆以下格式的 JSON（不要加任何其他文字）：
{"item": "iron_axe"}

通用原則：
- 稀有資源（diamond、gold_ingot、netherite）優先保留，除非沒有其他選擇
- 選擇材料最充裕的選項，避免把珍貴資源用在可以用普通材料替代的地方
- 考慮整體資源分配：某種材料如果只夠做一件東西，要想清楚這件東西是否最重要
- 只能從 options 列表裡選一個，回傳 item 的英文名稱
- 如果背包有可燒製的原礦（raw_iron 等），考慮是否先燒成錠再合成更好的工具
"""

SMELTABLE = {
    'raw_iron': 'iron_ingot', 'iron_ore': 'iron_ingot', 'deepslate_iron_ore': 'iron_ingot',
    'raw_gold': 'gold_ingot', 'gold_ore': 'gold_ingot', 'deepslate_gold_ore': 'gold_ingot',
    'raw_copper': 'copper_ingot', 'copper_ore': 'copper_ingot', 'deepslate_copper_ore': 'copper_ingot',
    'sand': 'glass', 'cobblestone': 'stone',
}


async def handle(state: dict, llm: LLMClient) -> dict | None:
    inventory = state.get("inventory", [])
    goal = state.get("goal", "物品")
    options = state.get("options", [])
    activity = state.get("activity", "idle")
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
        "可燒製的原料（可先用 !smelt 指令處理）：\n" + "\n".join(smeltable_lines)
        if smeltable_lines else "（無可燒製原料）"
    )

    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory)
    prompt = (
        f"機器人需要合成：{goal}\n"
        f"目前可合成的選項：{', '.join(options)}\n\n"
        f"當前狀態：活動={activity}，位置 Y={y}，血量={health}/20，飢餓={food}/20\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"{smeltable_section}\n\n"
        f"請從選項中選出最適合的一個，必要時考慮是否先燒製原料能讓合成更好的工具。"
    )

    response = None
    try:
        print(f"[Skill/craft_decision] Prompt:\n{prompt}\n---")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = json.loads(clean)
        return {"command": "craft_decision", **decision}
    except Exception as e:
        print(f"[Skill/craft_decision] 解析失敗: {e}\n原始回應: {response!r}")
        return None
