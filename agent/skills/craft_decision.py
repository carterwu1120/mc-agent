import json
import re
from agent import task_memory
from agent.brain import LLMClient
from agent.context_builder import build_for_skill
from agent.plan_utils import normalize_commands
from agent.prompt_logging import log_prompt
from agent.skills.command_validation import PLAN_ALLOWED_COMMANDS, validate_commands
from agent.skills.commands_ref import command_list
from agent.skills.knowledge import knowledge
from agent.skills.llm_response import parse_llm_json
from agent.skills.state_summary import summary_json


def _parse_json(text: str) -> dict:
    """Parse JSON from LLM response, handling trailing commentary after last field."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # LLM sometimes appends notes after the last string value before }:
        # {"key": "value"<extra text>}  →  truncate at error pos and close
        truncated = text[:e.pos].rstrip()
        if truncated.endswith('"'):
            try:
                return json.loads(truncated + '}')
            except json.JSONDecodeError:
                pass
        raise

SYSTEM_PROMPT = """你是 Minecraft 機器人的合成決策助手。
機器人需要合成某樣物品，但有多種材料選項，請選出最適合的一個。
只能回覆以下格式的 JSON（不要加任何其他文字）：
{"item": "iron_axe"}

""" + knowledge("crafting", "選擇合成選項")

_MATERIAL_ALLOWED = PLAN_ALLOWED_COMMANDS - {"equip"}

SYSTEM_PROMPT_MATERIAL = """你是 Minecraft 合成前置條件規劃者。
機器人缺少合成材料，請規劃材料取得路徑。原任務若仍存在，會在前置步驟完成後自動接回。
只輸出以下其中一種 JSON，不要加其他文字：
{"action":"plan","commands":["..."],"text":"策略理由"}
{"action":"wait","text":"目前無可行路徑，以及需要玩家提供什麼"}

""" + knowledge("crafting", "缺少合成材料") + "\n\n" + knowledge("bot_capabilities", "鎬子與工具鏈", "冶煉與燃料") + """

可用指令：
""" + command_list(sorted(_MATERIAL_ALLOWED), include_stops=False)

SMELTABLE = {
    'raw_iron': 'iron_ingot', 'iron_ore': 'iron_ingot', 'deepslate_iron_ore': 'iron_ingot',
    'raw_gold': 'gold_ingot', 'gold_ore': 'gold_ingot', 'deepslate_gold_ore': 'gold_ingot',
    'raw_copper': 'copper_ingot', 'copper_ore': 'copper_ingot', 'deepslate_copper_ore': 'copper_ingot',
    'sand': 'glass', 'cobblestone': 'stone',
}


async def handle(state: dict, llm: LLMClient) -> dict | None:
    inventory = state.get("inventory", [])
    goal      = state.get("goal", "物品")
    options   = state.get("options", [])
    reason    = state.get("reason", "choose")
    activity  = state.get("activity", "idle")
    pos       = state.get("pos") or {}
    health    = state.get("health", "?")
    food      = state.get("food", "?")
    y         = round(pos.get("y", 0))

    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory) or "（空背包）"

    # ── 材料不足，決定如何補充 ──────────────────────────────
    if reason == "material_missing" or not options:
        missing = state.get("missing_materials", [])
        missing_lines = "\n".join(f"- {m['name']} x{m['count']}" for m in missing) or "（未指定）"
        task_history = build_for_skill(
            "craft_decision",
            task_memory.recent_events(),
            task_memory.recent_failures(),
        )
        prompt = (
            f"機器人想合成「{goal}」，但缺少以下材料：\n{missing_lines}\n\n"
            f"當前狀態：活動={activity}，位置 Y={y}，血量={health}/20，飢餓={food}/20\n\n"
            f"背包內容：\n{inv_summary}\n\n"
            f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
            f"{task_history}"
            f"請根據缺少的材料類型，決定下一步行動。"
        )
        response = None
        try:
            log_prompt("Skill/craft_decision/material", prompt)
            response = await llm.chat(
                [{"role": "user", "content": prompt}],
                system=SYSTEM_PROMPT_MATERIAL,
                reasoning_effort="medium",
            )
            clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
            clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
            decision = parse_llm_json(_parse_json(clean), "Skill/craft_decision/material")
            text = decision.get("text", "").strip()
            if not text:
                raise ValueError("material decision needs a reason")
            if decision.get("action") == "wait":
                return {"command": "chat", "text": text}
            commands = decision.get("commands")
            if decision.get("action") != "plan" or not isinstance(commands, list) or not 1 <= len(commands) <= 6:
                raise ValueError("material decision must be plan with 1-6 commands, or wait")
            commands = normalize_commands(commands)
            errors = validate_commands(commands, allowed_commands=_MATERIAL_ALLOWED)
            if errors:
                raise ValueError(str(errors))
            active_task = task_memory.load()
            preserve_task = bool(active_task and active_task.get("status") == "running")
            if preserve_task:
                commands.append("resumetask")
            return [
                {"command": "chat", "text": text},
                {
                    "action": "plan",
                    "commands": commands,
                    "goal": f"取得 {goal} 所需材料",
                    "preserve_task": preserve_task,
                },
            ]
        except Exception as e:
            print(f"[Skill/craft_decision/material] 解析失敗: {e}\n原始回應: {response!r}")
            return None

    # ── 有多個選項，選最適合的 ──────────────────────────────
    inv_map = {i['name']: i['count'] for i in inventory}
    smeltable_lines = [
        f"- {name} x{inv_map[name]} → {out}"
        for name, out in SMELTABLE.items() if name in inv_map
    ]
    smeltable_section = (
        "可燒製的原料（可先用 !smelt 指令處理）：\n" + "\n".join(smeltable_lines)
        if smeltable_lines else "（無可燒製原料）"
    )

    task_history = build_for_skill(
        "craft_decision",
        task_memory.recent_events(),
        task_memory.recent_failures(),
    )
    prompt = (
        f"機器人需要合成：{goal}\n"
        f"目前可合成的選項：{', '.join(options)}\n\n"
        f"當前狀態：活動={activity}，位置 Y={y}，血量={health}/20，飢餓={food}/20\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"{smeltable_section}\n\n"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
        f"{task_history}"
        f"請從選項中選出最適合的一個，必要時考慮是否先燒製原料能讓合成更好的工具。"
    )

    response = None
    try:
        log_prompt("Skill/craft_decision", prompt)
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
            reasoning_effort="low",
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = parse_llm_json(json.loads(clean), "Skill/craft_decision")
        return {"command": "craft_decision", **decision}
    except Exception as e:
        print(f"[Skill/craft_decision] 解析失敗: {e}\n原始回應: {response!r}")
        return None
