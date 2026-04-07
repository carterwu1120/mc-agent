import json
import re
from agent.brain import LLMClient
from agent.skills.state_summary import summary_json, equipment_summary
from agent.skills.commands_ref import command_list

_DURABILITY_COMMANDS = command_list(["mine", "chop", "smelt", "equip", "chat", "idle"])

SYSTEM_PROMPT = f"""你是 Minecraft 機器人的裝備耐久度處理助手。
一或多件裝備（主手工具、頭盔、胸甲、護腿、靴子）耐久度剩餘 10% 以下，請根據目前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：

{{"action": "plan", "commands": ["mine iron 3", "smelt raw_iron 3"], "text": "...理由..."}}
{{"command": "equip", "text": "...理由..."}}
{{"command": "chat", "text": "...告知玩家..."}}
{{"command": "idle", "text": "...理由..."}}

【可用指令】
{_DURABILITY_COMMANDS}

【裝備合成材料對應】
- 木製工具/盔甲：需要 planks（木板）
- 石製工具：需要 cobblestone（鵝卵石）
- 鐵製工具/盔甲：需要 iron_ingot（鐵錠），鐵錠來自 smelt raw_iron
- 鑽石工具/盔甲：需要 diamond（鑽石），無法冶煉取得，只能挖礦
- 盔甲（頭盔/胸甲/護腿/靴子）是直接用材料合成，不需要冶煉步驟

【決策原則】
- 若背包有備用裝備可替換 → equip 換上
- 若背包有足夠材料可以合成新裝備 → equip（bot 會自動合成）
- 若沒有材料：
  - 鑽石裝備 → 需要 mine diamond，沒有捷徑
  - 鐵製裝備 → plan: mine iron → smelt raw_iron → equip
  - 若取得材料成本太高（需要深挖），chat 告知玩家由玩家決定
- 若裝備還有耐久（只是偏低）且任務重要 → idle 讓玩家決定
- 若是斧頭/劍等非必要工具，且任務不需要它 → idle
- 禁止中斷正在進行的採礦/釣魚等長時任務，除非裝備即將完全損壞
- 絕對不要挖石頭來處理盔甲問題，鵝卵石對盔甲無用
"""


async def handle(state: dict, llm: LLMClient) -> list | dict | None:
    items: list[dict] = state.get('items') or []
    # Fallback for old single-item format
    if not items and state.get('item'):
        items = [{'item': state['item'], 'durability_pct': state.get('durability_pct', 0)}]

    if not items:
        return None

    activity = state.get('activity', 'idle')
    inventory = state.get('inventory', [])

    items_summary = "\n".join(f"- {i['item']}：耐久 {i['durability_pct']}%" for i in items)
    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory) or "（空背包）"
    equip_summary = equipment_summary(state)
    item_names = "、".join(i['item'] for i in items)

    prompt = (
        f"耐久度不足的裝備：\n{items_summary}\n\n"
        f"目前活動：{activity}\n\n"
        f"目前裝備：\n{equip_summary}\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
        f"請決定機器人接下來要怎麼處理裝備耐久問題。"
    )

    response = None
    try:
        print(f"[Durability] {item_names} 耐久不足，詢問 LLM")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        if not clean.endswith("}"):
            clean += "}"
        decision = json.loads(clean)

        text = decision.get("text", "").strip()
        result = []

        if decision.get("action") == "plan":
            commands = decision.get("commands", [])
            if text:
                result.append({"command": "chat", "text": text})
            if commands:
                result.append({"action": "plan", "commands": commands, "goal": f"修復裝備 {item_names}"})
            return result or None

        if decision.get("command") == "chat":
            return [{"command": "chat", "text": text}] if text else None

        if decision.get("command") == "idle":
            if text:
                result.append({"command": "chat", "text": text})
            return result or None

        cmd = {k: v for k, v in decision.items() if k != "text"}
        if text:
            result.append({"command": "chat", "text": text})
        result.append(cmd)
        return result or None

    except Exception as e:
        print(f"[Durability] 解析失敗: {e}\n原始回應: {response!r}")

    return None
