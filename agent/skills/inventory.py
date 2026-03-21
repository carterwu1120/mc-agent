import json
import re
from agent.brain import LLMClient

SYSTEM_PROMPT = """你是 Minecraft 機器人的背包管理助手。
背包已滿，你需要決定怎麼處理。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"action": "drop", "items": ["lily_pad", "rotten_flesh"]}
{"action": "continue"}

規則：
- drop：列出可以原地丟棄的垃圾物品名稱（英文 item name）
- continue：所有東西都有用，無法丟棄
- 垃圾定義：無法使用、無法合成、無實際價值的物品（如 lily_pad, tripwire_hook, string 等）
- 食物、工具、武器、建材、珍貴材料（礦石、稀有物品）不要丟
- 根據目前活動判斷哪些東西相對沒用
- 如果沒有垃圾可丟，回傳 continue
"""


async def handle(state: dict, llm: LLMClient) -> dict | None:
    inventory = state.get("inventory", [])
    activity = state.get("activity", "idle")

    prompt = (
        f"背包已滿，機器人原本正在進行的活動：{activity}。\n"
        f"請根據目前的活動判斷哪些物品是垃圾可以丟掉。\n\n"
        f"背包內容：\n"
        + "\n".join(f"- {i['name']} x{i['count']}" for i in inventory)
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
