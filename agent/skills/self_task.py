import json
import re
from agent.brain import LLMClient
from agent.skills.llm_response import parse_llm_json
from agent.skills.state_summary import summary_json
from agent.skills.commands_ref import command_list
from agent import task_memory
from agent import exploration_memory
from agent.context_builder import (
    build_for_skill,
)

_SELF_TASK_COMMANDS = command_list(["getfood", "chop", "mine", "smelt", "equip", "home", "deposit", "explore", "tp", "idle"])

SYSTEM_PROMPT = f"""你是 Minecraft 陪玩型 agent 的自主任務規劃助手。
機器人目前沒有玩家直接指定的新任務，請根據它自己的狀態，決定下一個最合理的生存/補給任務。

只能回覆以下其中一種 JSON（不要加其他文字）：
{{"command":"getfood","goal":{{"count":8}},"text":"..."}}
{{"command":"idle","text":"..."}}

或 plan（步驟數根據需求決定，不設上限）：
{{"action":"plan","commands":["tp 376 -55 -1078","mine diamond 10","equip"],"goal":"補充鑽石工具","text":"..."}}

【可用指令】
{_SELF_TASK_COMMANDS}

【優先順序】
1. 食物危機（cooked_total < 3）→ 立即 getfood
2. 裝備損壞（low_durability_equipment 不為空）→ equip
3. 工具鏈補充（有明確缺口）→ 規劃 plan 補齊
4. 背包有已分類箱子且空間快滿 → home + deposit
5. 無迫切需求 → idle

【工具鏈推理】
收到需求前先檢查背包，確認缺什麼才加對應步驟：
- 缺鐵鎬（iron_pickaxe）→ mine iron 16 → smelt iron 16 → equip
- 缺石鎬（stone_pickaxe）→ mine stone 16 → equip
- 缺食物 → getfood 或 smelt（若有 raw food + 燃料）
- 缺木材（logs < 16）→ chop logs 32
- 有大量原礦未冶煉（raw_iron > 16）→ smelt iron <count>

【中途補料原則】
計畫執行到一半需要插入 smelt 或 craft 步驟時：
- 查背包有多少該原料（raw_iron / raw_food / 任何 raw_*），一次燒完或燒合理大批次，不要只燒剛好夠這一次用的最小量
- 多出來的中間材料（iron_ingot 等）下次可以直接用，減少未來再插入 smelt 的需要
- 計畫裡不應出現多次相同 smelt 指令（代表沒有一次備足）
- 數量由 LLM 根據背包庫存和實際需求決定，沒有固定數字

【善用已知資源位置】
若 prompt 中有「已知資源位置」段落，代表 bot 過去找到過這些資源：
- 需要挖礦時：優先 tp 到已知礦物位置，再執行 mine，而不是隨機 explore
- 需要砍樹時：優先 tp 到已知森林位置
- 需要補食物時：若有已知動物區域，可加 tp 再 hunt/getfood
- tp 格式：tp <x> <y> <z>（整數座標）

【禁止】
- 不要輸出 chat、follow、withdraw、combat、fish
- 不要規劃沒有明確缺口的步驟（背包夠用就 idle）
- 不要假設缺少實際上已有的物品（先看 inventory）
"""

ALLOWED_COMMANDS = {"getfood", "chop", "mine", "smelt", "equip", "home", "deposit", "explore", "tp", "idle"}


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
    if not isinstance(commands, list) or not commands:
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

    # 所有模式：若有未完成任務，優先恢復（避免新計畫蓋掉舊任務）
    task = task_memory.load()
    if task and task.get("status") == "interrupted":
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
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        try:
            raw = json.loads(clean)
        except json.JSONDecodeError:
            raw = _extract_first_json_object(clean)
        decision = _normalize_result(parse_llm_json(raw, "SelfTask"))

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
