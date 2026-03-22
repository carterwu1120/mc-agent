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
"""


async def handle(state: dict, llm: LLMClient) -> dict | None:
    inventory = state.get("inventory", [])
    goal = state.get("goal", "物品")
    options = state.get("options", [])

    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory)
    prompt = (
        f"機器人需要合成：{goal}\n"
        f"目前可合成的選項：{', '.join(options)}\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"請從選項中選出最適合的一個。"
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
