import json
import re
from agent import task_memory
from agent.brain import LLMClient
from agent.context_builder import build_for_skill
from agent.handlers.llm_response import parse_llm_json
from agent.handlers.state_summary import summary_json, equipment_summary
from agent.handlers.commands_ref import command_list
from agent.handlers.knowledge import knowledge
from agent.handlers.prompts import render_prompt

_DURABILITY_COMMANDS = command_list(["mine", "chop", "smelt", "preparetool", "makearmor", "equip", "chat", "idle"])

SYSTEM_PROMPT = render_prompt("tool_durability", commands=_DURABILITY_COMMANDS, durability=knowledge("durability"))


async def handle(state: dict, llm: LLMClient) -> list | dict | None:
    items: list[dict] = state.get('items') or []
    # Fallback for old single-item format
    if not items and state.get('item'):
        items = [{'item': state['item'], 'durability_pct': state.get('durability_pct', 0)}]

    if not items:
        return None

    activity = state.get('activity', 'idle')
    inventory = state.get('inventory', [])

    # Mining owns pickaxe replacement and can craft the correct tier using live
    # terrain/inventory state. A parallel LLM equip command races that recovery
    # and cannot help now that equip intentionally never crafts.
    if activity == 'mining' and all(
        str(item.get('item') or '').endswith('_pickaxe') for item in items
    ):
        print('[Durability] 採礦中的稿子耐久不足，交由 mining 本地更換，不呼叫 LLM')
        return None

    items_summary = "\n".join(f"- {i['item']}：耐久 {i['durability_pct']}%" for i in items)
    def _inv_line(i):
        line = f"- {i['name']} x{i['count']}"
        pct = i.get('durability_pct')
        if pct is not None and pct <= 10:
            line += f" ⚠ 耐久 {pct}%（已損壞，不可用）"
        return line
    inv_summary = "\n".join(_inv_line(i) for i in inventory) or "（空背包）"
    equip_summary = equipment_summary(state)
    item_names = "、".join(i['item'] for i in items)

    prompt = (
        f"耐久度不足的裝備：\n{items_summary}\n\n"
        f"目前活動：{activity}\n\n"
        f"目前裝備：\n{equip_summary}\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
        f"請決定機器人接下來要怎麼處理裝備耐久問題。"
        f"{build_for_skill('tool_durability', task_memory.recent_events(), task_memory.recent_failures())}"
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
        decision = parse_llm_json(json.loads(clean), "Durability")

        text = decision.get("text", "").strip()
        result = []

        if decision.get("action") == "plan":
            commands = list(decision.get("commands", []) or [])
            if text:
                result.append({"command": "chat", "text": text})
            if commands:
                if "resumetask" not in commands:
                    commands.append("resumetask")
                result.append({
                    "action": "plan",
                    "commands": commands,
                    "goal": f"修復裝備 {item_names}",
                    "preserve_task": True,
                })
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
