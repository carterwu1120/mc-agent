import json
import re
from agent.brain import LLMClient
from agent.handlers.llm_response import parse_llm_json
from agent.handlers.state_summary import summary_json, summarize_state
from agent.handlers.commands_ref import command_list
from agent.handlers.knowledge import knowledge
from agent.handlers.command_validation import validate_commands
from agent import task_memory
from agent import exploration_memory
from agent.context_builder import (
    build_for_skill,
)

_SELF_TASK_COMMAND_KEYS = [
    "hunt", "fish", "getfood", "chop", "mine", "smelt", "preparetool", "equip",
    "home", "deposit", "explore", "tp", "idle",
]
_SELF_TASK_COMMANDS = command_list(_SELF_TASK_COMMAND_KEYS)

SYSTEM_PROMPT = f"""你是 Minecraft 陪玩型 agent 的自主任務規劃助手。沒有玩家指定的任務時，依 bot 自己的狀態決定下一個最合理的生存或補給任務。
只能回覆以下其中一種 JSON（不要加其他文字）：
{{"command":"idle","text":"..."}}
{{"command":"equip","text":"..."}}
{{"action":"plan","commands":["..."],"goal":"...","text":"..."}}

【可用指令】
{_SELF_TASK_COMMANDS}

【優先順序】
1. 真正的食物危機（飢餓值低，且缺少可直接食用的食物）→ 優先建立可完成的補給計畫
2. 裝備損壞（low_durability_equipment 不為空）→ equip
3. 工具鏈補充（有明確缺口）→ 規劃 plan 補齊
4. 背包有已分類箱子且空間快滿 → home + deposit
5. 無迫切需求 → idle

【原則】
- 只為明確缺口規劃。先看 inventory，不要假設缺少已有的物品；背包夠用就 idle。
- 缺工具時依配方與現有材料精算，只取得夠做工具加上合理緩衝的材料。
- 木材少、原礦未冶煉本身不是任務；只有目前目標需要時，才 chop 或 smelt 實際需要的量。
- 需要插入 smelt 時，一次把該原料燒成合理的大批次（多出的錠下次可用），計畫中不要出現重複的 smelt。
- prompt 有「已知資源位置」時，需要挖礦、砍樹或補食物就優先 tp 到該位置，而不是隨機 explore。
- 不要輸出 chat、follow、withdraw、combat。

{knowledge('bot_capabilities', '鎬子與工具鏈', '食物')}
"""

ALLOWED_COMMANDS = set(_SELF_TASK_COMMAND_KEYS)


def _extract_first_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = decoder.raw_decode(text[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    raise json.JSONDecodeError("No valid JSON object found", text, 0)


def _normalize_result(result: dict) -> dict:
    """Normalize shape only; never invent an action quantity."""
    return result


def _positive_int(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and int(value) == value
        and value > 0
    )


def _is_valid_command_result(result: dict) -> bool:
    command = result.get("command")
    if command not in ALLOWED_COMMANDS:
        return False

    if command in {"getfood", "hunt"}:
        goal = result.get("goal") or {}
        return isinstance(goal, dict) and _positive_int(goal.get("count"))

    if command == "fish":
        goal = result.get("goal") or {}
        return isinstance(goal, dict) and _positive_int(goal.get("catches"))

    if command == "chop":
        goal = result.get("goal") or {}
        return isinstance(goal, dict) and _positive_int(goal.get("logs"))

    if command == "mine":
        args = result.get("args") or []
        return (
            isinstance(args, list)
            and len(args) >= 2
            and isinstance(args[0], str)
            and isinstance(args[1], str)
            and args[1].isdigit()
            and int(args[1]) > 0
        )

    if command == "smelt":
        goal = result.get("goal") or {}
        return (
            isinstance(goal, dict)
            and isinstance(goal.get("target"), str)
            and _positive_int(goal.get("count"))
        )

    return True


def _is_valid_plan_result(result: dict) -> bool:
    if result.get("action") != "plan":
        return False
    commands = result.get("commands")
    if not isinstance(commands, list) or not commands:
        return False
    return not validate_commands(commands, allowed_commands=ALLOWED_COMMANDS - {"idle"})


def _food_decision_error(result: dict, state: dict) -> str | None:
    """Reject food actions that cannot make progress from the current inventory."""
    summary = summarize_state(state)
    food = summary["resources"]["food"]
    raw_total = int(food.get("raw_total", 0) or 0)
    inventory_names = {
        item.get("name") for item in (state.get("inventory") or []) if item.get("name")
    }

    if result.get("action") == "plan":
        commands = result.get("commands") or []
    else:
        command = result.get("command")
        if command == "getfood":
            commands = [f"getfood count {int(result['goal']['count'])}"]
        elif command == "hunt":
            commands = [f"hunt count {int(result['goal']['count'])}"]
        elif command == "fish":
            commands = [f"fish catches {int(result['goal']['catches'])}"]
        else:
            commands = []

    planned_raw = raw_total
    for command in commands:
        parts = command.split()
        name = parts[0] if parts else ""
        if name == "fish":
            if "fishing_rod" not in inventory_names:
                return "背包沒有 fishing_rod，不能選擇 fish"
            planned_raw += int(parts[2])
        elif name == "hunt":
            # Use a conservative one-food-per-target estimate. Actual drops may
            # be higher, but a plan must not depend on that luck to be viable.
            planned_raw += int(parts[2])
        elif name == "getfood":
            requested = int(parts[2])
            if planned_raw < requested:
                return (
                    f"getfood 需要足夠生食：目前 raw_total={raw_total}，"
                    f"計畫最多準備 {planned_raw}，但要求製作 {requested}；"
                    "請增加 hunt/fish 數量，必要時先 explore animals"
                )
            planned_raw -= requested
    return None


def _decision_error(decision: dict, state: dict) -> str | None:
    if decision.get("action") == "plan":
        if not _is_valid_plan_result(decision):
            return "plan 含有不合法、缺少數量或格式錯誤的指令"
    elif not _is_valid_command_result(decision):
        return "command 不合法或缺少明確的正整數 goal"
    return _food_decision_error(decision, state)


async def handle(state: dict, llm: LLMClient) -> dict | None:
    if state.get("activity") != "idle":
        return None

    mode = state.get("mode", "survival")

    # companion mode：不自主規劃
    if mode == "companion":
        return None

    # 所有模式：若有未完成任務，優先恢復（避免新計畫蓋掉舊任務）
    # 只恢復可恢復原因的中斷（crash/death），玩家主動停止不自動恢復
    _RESUMABLE_INTERRUPTIONS = {None, "restart", "death", "temporary_recovery"}
    task = task_memory.load()
    if task and task.get("status") == "interrupted":
        interrupted_by = task.get("interruptedBy")
        if interrupted_by not in _RESUMABLE_INTERRUPTIONS:
            print(f"[SelfTask] 跳過恢復：任務由 '{interrupted_by}' 中斷，需玩家明確指示")
        else:
            steps = task.get("steps", [])
            remaining = [s["cmd"] for s in steps if s.get("status") not in ("done", "failed")]
            if remaining:
                print(f"[SelfTask] 有未完成任務，自動恢復: {task['goal']}")
                return {"action": "plan", "commands": remaining, "goal": task["goal"], "resume_task": True}


    mem_summary = exploration_memory.summary_for_prompt()
    mem_section = f"\n【已知資源位置（探索記憶）】\n{mem_summary}\n" if mem_summary else ""

    task_history = build_for_skill("self_task", task_memory.recent_events(), task_memory.recent_failures(), task_memory.interrupted_tasks())

    prompt = (
        "請根據以下機器人狀態摘要，決定下一個自主任務。\n\n"
        f"{summary_json(state)}"
        f"{mem_section}"
        f"{task_history}"
    )

    response = None
    try:
        print("[SelfTask] 進行自主任務評估")
        decision = None
        request_prompt = prompt
        for attempt in range(2):
            response = await llm.chat(
                [{"role": "user", "content": request_prompt}],
                system=SYSTEM_PROMPT,
                reasoning_effort="low",
            )
            clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
            clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
            try:
                raw = json.loads(clean)
            except json.JSONDecodeError:
                raw = _extract_first_json_object(clean)
            decision = _normalize_result(parse_llm_json(raw, "SelfTask"))
            error = _decision_error(decision, state)
            if not error:
                break
            print(f"[SelfTask] 決策不可執行: {error}")
            if attempt == 1:
                return None
            request_prompt = (
                f"{prompt}\n\n上一個決策：{json.dumps(decision, ensure_ascii=False)}\n"
                f"不可執行原因：{error}\n"
                "請根據相同狀態重新輸出可完成的 JSON；不要省略數量，也不要直接重複原決策。"
            )

        if decision is None:
            return None

        if decision.get("action") == "plan":
            print(f"[SelfTask] 計畫: {decision.get('commands')}")
            return decision

        if decision.get("command") == "idle":
            print("[SelfTask] 決定保持 idle")
            return None

        text = decision.get("text", "").strip()
        if text:
            return [
                {"command": "chat", "text": text},
                {k: v for k, v in decision.items() if k != "text"},
            ]
        return decision
    except Exception as e:
        print(f"[SelfTask] 解析失敗: {e}\n原始回應: {response!r}")
        return None
