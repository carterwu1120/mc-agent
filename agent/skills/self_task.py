import json
import re
from agent.brain import LLMClient
from agent.skills.state_summary import summary_json
from agent.skills.commands_ref import command_list

_SELF_TASK_COMMANDS = command_list(["getfood", "chop", "mine", "smelt", "equip", "idle"])
from agent import task_memory

SYSTEM_PROMPT = f"""你是 Minecraft 陪玩型 agent 的自主任務規劃助手。
機器人目前沒有玩家直接指定的新任務，請根據它自己的狀態，決定下一個最合理的生存/補給任務。

你的目標：
1. 保持有足夠食物
2. 保持基本工具與裝備
3. 背包不要太亂或太滿
4. 沒有迫切需要時保持 idle

只能回覆以下其中一種 JSON（不要加其他文字）：
{{"command":"getfood","goal":{{"count":8}},"text":"..."}}
{{"command":"chop","goal":{{"logs":8}},"text":"..."}}
{{"command":"mine","args":["stone","8"],"text":"..."}}
{{"command":"mine","args":["iron","8"],"text":"..."}}
{{"command":"smelt","goal":{{"target":"beef","count":6}},"text":"..."}}
{{"command":"equip","text":"..."}}
{{"command":"tidy","text":"..."}}
{{"command":"idle","text":"..."}}

或短 plan（最多 3 步）：
{{"action":"plan","commands":["mine stone 8","smelt beef 6"],"text":"..."}}

【可用指令】
{_SELF_TASK_COMMANDS}

規則：
- 只能使用白名單指令：getfood, chop, mine, smelt, equip, tidy, idle
- 若 resources.food.cooked_total 很低，優先 getfood
- 若有大量 raw food 且 capabilities.can_smelt_food 為 true，可優先 smelt
- 若 capabilities.has_good_weapon 為 false 或 capabilities.has_good_armor 為 false，可優先 equip
- 若 capabilities.low_durability_equipment 不為空（裝備快壞了），優先 equip 換上備用
- 若背包已有足夠食物、工具、裝備，優先 idle
- 若缺少熔爐所需材料，可用 mine stone <count>
- 若缺少木材，可用 chop
- plan 最多 3 步，且只能用來補足明確缺口
- 不要輸出 chat、follow、home、withdraw、deposit、combat
"""

ALLOWED_COMMANDS = {"getfood", "chop", "mine", "smelt", "equip", "tidy", "idle"}


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
    if result.get("command") == "getfood" and "goal" not in result:
        result = {**result, "goal": {"count": 8}}
    if result.get("command") == "chop" and "goal" not in result and "args" not in result:
        result = {**result, "goal": {"logs": 8}}
    if result.get("command") == "mine":
        args = result.get("args") or []
        if len(args) == 1:
            result = {**result, "args": [args[0], "8"]}
    if result.get("action") == "plan":
        commands = result.get("commands", [])
        result["commands"] = commands[:3]
    return result


def _is_valid_command_result(result: dict) -> bool:
    command = result.get("command")
    if command not in ALLOWED_COMMANDS:
        return False

    if command == "getfood":
        goal = result.get("goal") or {}
        return isinstance(goal, dict) and isinstance(goal.get("count"), int | float)

    if command == "chop":
        goal = result.get("goal") or {}
        return isinstance(goal, dict) and isinstance(goal.get("logs"), int | float)

    if command == "mine":
        args = result.get("args") or []
        return (
            isinstance(args, list)
            and len(args) >= 2
            and isinstance(args[0], str)
            and isinstance(args[1], str)
        )

    if command == "smelt":
        goal = result.get("goal") or {}
        return (
            isinstance(goal, dict)
            and isinstance(goal.get("target"), str)
            and isinstance(goal.get("count"), int | float)
        )

    return True


def _is_valid_plan_result(result: dict) -> bool:
    if result.get("action") != "plan":
        return False
    commands = result.get("commands")
    if not isinstance(commands, list) or not commands or len(commands) > 3:
        return False
    for cmd in commands:
        if not isinstance(cmd, str):
            return False
        parts = cmd.split()
        if not parts or parts[0] not in ALLOWED_COMMANDS - {"idle"}:
            return False
    return True


async def handle(state: dict, llm: LLMClient) -> dict | None:
    if state.get("activity") != "idle":
        return None

    mode = state.get("mode", "survival")

    # companion mode：不自主規劃
    if mode == "companion":
        return None

    # workflow mode：優先恢復未完成任務
    if mode == "workflow":
        task = task_memory.load()
        if task and task.get("status") == "interrupted":
            remaining = task["commands"][task["currentStep"]:]
            if remaining:
                print(f"[SelfTask] workflow 模式，自動恢復任務: {task['goal']}")
                return {"action": "plan", "commands": remaining, "goal": task["goal"]}


    prompt = (
        "請根據以下機器人狀態摘要，決定下一個自主任務。\n\n"
        f"{summary_json(state)}"
    )

    response = None
    try:
        print("[SelfTask] 進行自主任務評估")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        try:
            decision = json.loads(clean)
        except json.JSONDecodeError:
            decision = _extract_first_json_object(clean)
        decision = _normalize_result(decision)

        if decision.get("action") == "plan":
            if not _is_valid_plan_result(decision):
                print(f"[SelfTask] 無效 plan，忽略: {decision}")
                return None
            print(f"[SelfTask] 計畫: {decision.get('commands')}")
            return decision

        if not _is_valid_command_result(decision):
            print(f"[SelfTask] 無效 command，忽略: {decision}")
            return None

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
