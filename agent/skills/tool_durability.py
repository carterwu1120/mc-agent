import json
import re
from agent.brain import LLMClient
from agent.skills.state_summary import summary_json, equipment_summary
from agent.skills.commands_ref import command_list

_DURABILITY_COMMANDS = command_list(["mine", "chop", "smelt", "equip", "chat", "idle"])

SYSTEM_PROMPT = f"""你是 Minecraft 機器人的工具耐久度處理助手。
機器人主手工具耐久度剩餘 10% 以下，請根據目前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：

{{"action": "plan", "commands": ["mine iron 3", "smelt raw_iron 3"], "text": "...理由..."}}
{{"command": "equip", "text": "...理由..."}}
{{"command": "chat", "text": "...告知玩家..."}}
{{"command": "idle", "text": "...理由..."}}

【可用指令】
{_DURABILITY_COMMANDS}

【決策原則】
- 若背包有同類型備用工具（例如有備用鎬）→ equip 換上
- 若背包有足夠材料可以合成新工具 → 用 plan 規劃 smelt + equip 或直接 equip
- 若沒有材料，根據工具類型決定去取得材料（挖鐵礦 → 冶煉 → equip）
- 若目前工具還有耐久（只是偏低）且任務重要，可以 idle 讓玩家決定
- 若工具是斧頭/劍等非必要工具，且任務不需要它 → idle
- 禁止中斷正在進行的採礦/釣魚等長時任務，除非工具即將完全損壞
"""


async def handle(state: dict, llm: LLMClient) -> list | dict | None:
    item_name = state.get('item', '未知工具')
    durability_pct = state.get('durability_pct', 0)
    activity = state.get('activity', 'idle')
    inventory = state.get('inventory', [])

    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory) or "（空背包）"
    equip_summary = equipment_summary(state)

    prompt = (
        f"主手工具：{item_name}，耐久度剩餘 {durability_pct}%\n"
        f"目前活動：{activity}\n\n"
        f"目前裝備：\n{equip_summary}\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
        f"請決定機器人接下來要怎麼處理工具耐久問題。"
    )

    response = None
    try:
        print(f"[Durability] {item_name} 耐久 {durability_pct}%，詢問 LLM")
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
                result.append({"action": "plan", "commands": commands, "goal": f"修復工具 {item_name}"})
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
