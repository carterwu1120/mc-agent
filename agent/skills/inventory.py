import json
import re
from agent.brain import LLMClient

SYSTEM_PROMPT = """你是 Minecraft 機器人的背包管理助手。
背包已滿，請決定哪些物品可以埋入地下清出空間，或是否回基地整理。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"action": "drop", "items": ["diorite", "tuff"]}
{"action": "continue"}

【絕對不能丟的物品】
- 食物（cooked_beef、bread、fish 等）
- 鐵質以上工具與武器（iron_pickaxe、iron_axe、sword 等）
- 礦石與原礦（ore、raw_iron、raw_gold、raw_copper、lapis_lazuli、coal、diamond、emerald 等）
- 木材與木板（oak_log、planks 等，合成用）
- string（製作弓、釣竿必要材料）
- 任何稀有或有合成價值的物品

【可以丟的低成本工具（重新製作不耗費珍貴資源）】
- wooden_pickaxe、wooden_axe、wooden_shovel 等木製工具
- stone_pickaxe、stone_axe、stone_shovel 等石製工具
- 多餘的 crafting_table（留 1 個即可）
- bone、rotten_flesh、spider_eye 等無用戰利品

【封口材料規則（重要）】
- cobblestone、cobbled_deepslate 是用來封埋垃圾的洞口材料
- 挖礦時：總共至少留 1 組（64個）cobblestone，超出的部分才能丟棄
- 其他活動：至少留 2 組（128個）

【根據活動決定的丟棄邏輯】

如果活動是 mining（挖礦）：
- cobblestone、cobbled_deepslate：留 1 組（64個），超出部分丟棄
- diorite、andesite、granite、tuff、gravel、flint、pointed_dripstone、dirt、sand：全部丟棄
- 低成本工具：可丟棄
- 釣魚垃圾（lily_pad、tripwire_hook 等）：全部丟棄

如果活動是 fishing、woodcutting、smelting 或其他：
- cobblestone、cobbled_deepslate：留 2 組（128個），超出部分丟棄
- diorite、andesite、granite、tuff：若超過 32 個可丟棄，否則保留
- gravel、flint、pointed_dripstone、dirt：全部丟棄
- 釣魚垃圾（lily_pad、tripwire_hook 等）：全部丟棄

如果沒有東西可以丟，回傳 continue。
items 清單填英文 item name，不包含數量。
"""


async def handle(state: dict, llm: LLMClient) -> dict | None:
    inventory = state.get("inventory", [])
    activity = state.get("activity", "idle")
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    y = round(pos.get("y", 0))

    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory)
    prompt = (
        f"背包已滿，機器人目前的活動：{activity}，位置 Y={y}，血量={health}/20，飢餓={food}/20。\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"請根據活動規則決定要埋掉哪些物品。"
    )

    response = None
    try:
        print(f"[Skill/inventory] Prompt:\n{prompt}\n---")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = json.loads(clean)
        return {"command": "inventory_decision", **decision}
    except Exception as e:
        print(f"[Skill/inventory] 解析失敗: {e}\n原始回應: {response!r}")
        return None
