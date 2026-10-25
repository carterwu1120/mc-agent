import asyncio
import json
import re
from agent.brain import LLMClient
from agent.handlers.command_validation import (
    PLAN_ALLOWED_COMMANDS,
    build_reprompt_suffix,
    validate_commands,
)
from agent.handlers.state_summary import summary_json
from agent.handlers.commands_ref import command_list
from agent.handlers.knowledge import knowledge
from agent import task_memory
from agent.context_builder import (
    build_chests_summary,
    build_for_skill,
)
from agent.plan_utils import normalize_commands
from agent.handlers.llm_response import parse_llm_json

_PLANNER_ALLOWED_KEYS = [
    "mine", "chop", "fish", "hunt", "getfood", "smelt", "combat",
    "stopmine", "stopchop", "stopfish", "stopsmelt", "stopcombat", "stopsurface", "stopexplore",
    "home", "back", "surface", "explore",
    "deposit", "withdraw", "makechest", "labelchest", "preparetool", "equip", "come", "tp",
]
# come/tp get planner-specific guidance below instead of the generic registry line.
_PLANNER_COMMANDS = command_list([k for k in _PLANNER_ALLOWED_KEYS if k not in ("come", "tp")])

SYSTEM_PROMPT = f"""你是 Minecraft 機器人的任務規劃助手，把玩家的自然語言轉成機器人指令序列。
只能回覆以下其中一種 JSON（不含其他文字）：
{{"action": "plan", "goal": "簡短描述此次任務目標", "final_goal": "玩家的最終目標（若不明確或與前次相同可省略）", "reasoning": "為何這樣規劃（前置條件、缺少資源等）", "commands": ["chop logs 20", "mine iron 10"]}}
{{"action": "chat", "text": "我聽不懂你的意思"}}

【final_goal】
玩家這次對話的最終意圖（例如「鑽石裝備」），跨多個子任務有效。玩家明確提到或換了目標時設定；延續前次目標、閒聊或提問時省略（系統會自動繼承）。

【可用指令】
{_PLANNER_COMMANDS}
- come [player]：走路接近玩家；只在不適合傳送或玩家要求走過去時使用
- tp <x> <y> <z> | tp <player>：傳送到整數座標或玩家；玩家說「過來 / 來找我」時優先 tp <player>

【玩家意圖】
- 說停止、停下、stop → 停止當前活動；沒有活動就回 chat
- 要你回到地面、地表、上去 → surface
- 提問、打招呼或不是任務 → chat

【執行中任務合併】
新需求與當前或剛中斷任務的 pending mine 步驟使用同一資源（例如 mine diamond）時：
- pending mine 代表資源即將取得；不要因為背包現在沒有就回 chat 說沒材料。
- 在既有的 mine 上加量：合併量 = 原 pending 量（已扣進度）+ 新需求量，不要從 0 重算，也不要新增重複的 mine 或 equip。
  例：pending=["mine diamond 19","equip"]，新需求鎬+劍=5 → ["stopmine","mine diamond 24","equip"]
- 新需求有更早要做的前置步驟（例如補食物）時，不 stop 當前步驟，把前置步驟排在合併後的 mine 之前。

【前置條件】
依背包（inventory）與裝備狀態（capabilities）推斷前置步驟。盔甲缺口見狀態摘要的 armor_progress.diamond_missing / diamond_shortfall_for_full_set。需要說明的限制寫在 reasoning。

{knowledge('planning')}

{knowledge('bot_capabilities', '鎬子與工具鏈', '裝備（equip）', '食物', '冶煉與燃料', '木材與箱子', '移動與位置')}

【未完成任務】
prompt 有「未完成任務」區塊且玩家要「繼續」時，從待執行的第一步開始，不重複已完成的步驟：
- 距離近、不需調整 → 直接回傳待執行步驟
- 上次工作位置很遠（例如超過 100 格）→ 先 tp <lastX> <lastY> <lastZ>，再接待執行步驟
- 物品不足或情況已改變 → 依目前背包重新規劃
- 食物足以支撐剩餘任務時，不要加入補食步驟
- 玩家說「從這邊開始 / 不要回去」→ 視為新任務，不回上次位置
"""

RESUME_PATTERNS = [
    r"^\s*繼續\s*$",
    r"^\s*continue\s*$",
    r"^\s*resume\s*$",
    r"^\s*resumetask\s*$",
    r"^\s*繼續任務\s*$",
    r"^\s*繼續上次的\s*$",
    r"繼續.{0,6}任務",
    r"繼續.{0,6}中斷",
    r"接.{0,4}上次",
    r"接.{0,4}回來",
]

RESTART_FROM_HERE_PATTERNS = [
    r"從這邊開始",
    r"從這裡開始",
    r"在這邊開始",
    r"在這裡開始",
    r"就在這裡",
    r"就在這邊",
    r"不要回去",
    r"別回去",
    r"直接在這",
    r"直接在這裡",
    r"直接在這邊",
]

MINING_INTENT_PATTERNS = [
    r"挖礦",
    r"挖鐵",
    r"挖金",
    r"挖煤",
    r"挖鑽石",
    r"\bmine\b",
]

RETURN_TO_MINING_PATTERNS = [
    r"回去.*挖",
    r"回去.*mine",
    r"恢復.*挖",
    r"恢復.*mine",
    r"繼續.*挖",
    r"繼續.*mine",
    r"接著.*挖",
    r"接著.*mine",
    r"回來.*挖",
    r"回來.*mine",
]

COME_PATTERNS = [
    r"\bcome here\b",
    r"\bcome to me\b",
    r"過來",
    r"來我這",
    r"來我這裡",
    r"來找我",
    r"跟我來",
    r"跟上",
]

SURFACE_PATTERNS = [
    r"\bsurface\b",
    r"\bgo to surface\b",
    r"\bgo above ground\b",
    r"回到地面",
    r"回地表",
    r"到地面",
    r"到地表",
    r"上去",
    r"回到陸地",
]

STOP_PATTERNS = [
    r"^\s*stop\s*$",
    r"^\s*停止\s*$",
    r"^\s*停下(來)?\s*$",
    r"^\s*先停(下來)?\s*$",
    r"^\s*不要做了\s*$",
    r"^\s*暫停\s*$",
    r"^\s*先暫停\s*$",
    r"^\s*pause\s*$",
]

_TRANSIENT_LLM_ERROR_PATTERNS = (
    "503",
    "unavailable",
    "high demand",
    "try again later",
)


def _is_transient_llm_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(pattern in text for pattern in _TRANSIENT_LLM_ERROR_PATTERNS)


async def _chat_with_retry(
    llm: LLMClient,
    prompt: str,
    system: str,
    attempts: int = 3,
    reasoning_effort: str = "medium",
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await llm.chat(
                [{"role": "user", "content": prompt}],
                system=system,
                reasoning_effort=reasoning_effort,
            )
        except Exception as e:
            last_error = e
            if not _is_transient_llm_error(e) or attempt == attempts:
                raise
            delay = attempt
            print(f"[Planner] LLM 暫時不可用，第 {attempt}/{attempts} 次失敗，{delay}s 後重試: {e}")
            await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error


def _parse_decision_text(response: str) -> dict:
    clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
    clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
    try:
        raw = json.loads(clean)
        return parse_llm_json(raw, "Planner")
    except json.JSONDecodeError:
        pass
    # fallback: extract last {...} JSON block from markdown-heavy responses
    import re as _re
    blocks = _re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", clean, flags=_re.DOTALL)
    if blocks:
        raw = json.loads(blocks[-1])
        return parse_llm_json(raw, "Planner")
    # last resort: find last { ... } in the text
    last = clean.rfind("{")
    if last != -1:
        raw = json.loads(clean[last:clean.rfind("}") + 1])
        return parse_llm_json(raw, "Planner")
    raise json.JSONDecodeError("no JSON found", clean, 0)


def _planner_failure_chat() -> dict:
    return {"command": "chat", "text": "我這次規劃失敗了，請再說一次。"}


EQUIPMENT_ITEM_COSTS = {
    "helmet": 5,
    "chestplate": 8,
    "leggings": 7,
    "boots": 4,
    "sword": 2,
    "pickaxe": 3,
    "axe": 3,
    "shovel": 1,
    "hoe": 2,
}

EQUIPMENT_ALIASES = {
    "diamond": ("鑽石", "diamond"),
    "iron": ("鐵", "iron"),
    "stone": ("石", "stone"),
}

ARMOR_SET_ITEMS = ("helmet", "chestplate", "leggings", "boots")
TOOL_SET_ITEMS = ("pickaxe", "axe", "shovel", "hoe")


def _inventory_counts(state: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in (state.get("inventory") or []):
        name = item.get("name")
        if not name:
            continue
        counts[name] = counts.get(name, 0) + int(item.get("count", 0) or 0)
    return counts


def _matches_phrase(message: str, lowered: str, phrase: str) -> bool:
    if re.fullmatch(r"[a-z ]+", phrase):
        return re.search(rf"\b{re.escape(phrase)}\b", lowered) is not None
    return phrase in message or phrase in lowered


def _owned_equipment_items(state: dict, material: str) -> set[str]:
    owned: set[str] = set()
    inventory_counts = _inventory_counts(state)
    for name, count in inventory_counts.items():
        if count <= 0 or not name.startswith(f"{material}_"):
            continue
        for suffix in EQUIPMENT_ITEM_COSTS:
            if name.endswith(f"_{suffix}"):
                owned.add(suffix)
                break

    equipment = state.get("equipment") or {}
    main_hand = equipment.get("main_hand")
    if isinstance(main_hand, dict):
        name = main_hand.get("name")
        if name and name.startswith(f"{material}_"):
            for suffix in EQUIPMENT_ITEM_COSTS:
                if name.endswith(f"_{suffix}"):
                    owned.add(suffix)
                    break

    for piece in ((equipment.get("armor") or {}).values()):
        if not isinstance(piece, dict):
            continue
        name = piece.get("name")
        if name and name.startswith(f"{material}_"):
            for suffix in EQUIPMENT_ITEM_COSTS:
                if name.endswith(f"_{suffix}"):
                    owned.add(suffix)
                    break
    return owned


def _requested_equipment_items(message: str, lowered: str) -> list[str]:
    requested: list[str] = []

    armor_patterns = (
        "鑽石裝", "鑽石裝備", "鑽石盔甲", "一套鑽石裝",
        "鐵裝", "鐵裝備", "鐵盔甲", "一套鐵裝",
        "diamond armor", "diamond armour", "diamond armor set",
        "iron armor", "iron armour", "iron armor set",
    )
    if any(_matches_phrase(message, lowered, p) for p in armor_patterns):
        requested.extend(ARMOR_SET_ITEMS)

    tool_set_patterns = (
        "工具組", "工具套", "全套工具", "一套工具",
        "diamond tools", "iron tools", "stone tools", "tool set",
    )
    if any(_matches_phrase(message, lowered, p) for p in tool_set_patterns):
        requested.extend(TOOL_SET_ITEMS)

    item_patterns = (
        ("sword", ("劍", "sword")),
        ("pickaxe", ("稿", "鎬", "稿子", "pickaxe")),
        ("axe", ("斧", "axe")),
        ("shovel", ("鏟", "shovel")),
        ("hoe", ("鋤", "hoe")),
    )
    for item, patterns in item_patterns:
        if any(_matches_phrase(message, lowered, p) for p in patterns):
            requested.append(item)

    seen: set[str] = set()
    deduped: list[str] = []
    for item in requested:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _running_task_pending_mine_count(material: str) -> int:
    """Returns total pending mine count for the given material in the current or just-interrupted task.

    Also checks "interrupted" because task_arbitration interrupts the task before planner runs,
    so by the time planner is called the status is already "interrupted".
    """
    task = task_memory.load_any()
    if not task or task.get("status") not in ("running", "interrupted"):
        return 0
    for cmd in _task_remaining_commands(task):
        parts = (cmd or "").split()
        if len(parts) >= 3 and parts[0] == "mine" and parts[1] == material:
            try:
                return int(parts[2])
            except (ValueError, IndexError):
                pass
    return 0


def _equipment_goal_shortcut(message: str, state: dict, activity: str) -> dict | None:
    lowered = message.lower()
    material = next(
        (name for name, patterns in EQUIPMENT_ALIASES.items() if any(_matches_phrase(message, lowered, p) for p in patterns)),
        None,
    )
    if not material:
        return None

    requested_items = _requested_equipment_items(message, lowered)
    if not requested_items:
        return None
    if material == "stone" and any(item in ARMOR_SET_ITEMS for item in requested_items):
        return None

    # If there's a running task that already has pending mine steps for the same
    # material, skip the shortcut and let the LLM merge the tasks properly.
    pending_count = _running_task_pending_mine_count(material) if material in ("diamond", "iron") else 0
    print(f"[Planner] equipment shortcut: material={material}, pending_mine={pending_count}")
    if pending_count > 0:
        print(f"[Planner] 偵測到 running task 有 pending mine {material}，跳過 shortcut → LLM 合併")
        return None

    summary = json.loads(summary_json(state))
    cooked_total = int(((((summary.get("resources") or {}).get("food") or {}).get("cooked_total")) or 0))
    materials = ((summary.get("resources") or {}).get("materials") or {})
    owned_items = _owned_equipment_items(state, material)
    missing_items = [item for item in requested_items if item not in owned_items]
    needed_units = sum(EQUIPMENT_ITEM_COSTS[item] for item in missing_items)

    commands: list[str] = []
    stop_cmd = _stop_command_for_activity(activity)
    if activity not in (None, "idle") and stop_cmd:
        commands.append(stop_cmd)

    # Food preparation depends on hunger, inventory, route length and danger.
    # The deterministic shortcut cannot safely choose that quantity/path.
    if cooked_total < 5:
        print("[Planner] 熟食偏低，跳過 equipment shortcut → LLM 動態規劃食物缺口")
        return None

    if material == "diamond":
        have_units = int(materials.get("diamond", 0) or 0)
        shortfall = max(0, needed_units - have_units)
        if shortfall > 0:
            commands.append(f"mine diamond {shortfall}")
    elif material == "iron":
        iron_ingot = int(materials.get("iron_ingot", 0) or 0)
        raw_iron = int(materials.get("raw_iron", 0) or 0)
        total_iron_units = iron_ingot + raw_iron
        shortfall = max(0, needed_units - total_iron_units)
        if shortfall > 0:
            commands.append(f"mine iron {shortfall}")
        smelt_count = max(0, needed_units - iron_ingot)
        if smelt_count > 0:
            commands.append(f"smelt raw_iron {smelt_count}")
    elif material == "stone":
        cobblestone = int(materials.get("cobblestone", 0) or 0)
        shortfall = max(0, needed_units - cobblestone)
        if shortfall > 0:
            commands.append(f"mine stone {shortfall}")

    commands.append("equip")

    goal_material = {
        "diamond": "鑽石",
        "iron": "鐵",
        "stone": "石",
    }[material]
    goal_labels = {
        "helmet": "頭盔",
        "chestplate": "胸甲",
        "leggings": "護腿",
        "boots": "靴子",
        "sword": "劍",
        "pickaxe": "稿",
        "axe": "斧",
        "shovel": "鏟",
        "hoe": "鋤",
    }
    goal_text = f"製作{goal_material}" + "、".join(goal_labels[item] for item in requested_items)

    return {
        "action": "plan",
        "goal": goal_text,
        "commands": normalize_commands(commands),
    }


async def _reprompt_invalid_plan(
    llm: LLMClient,
    prompt: str,
    invalid_commands: list[str],
    errors,
) -> dict | None:
    reprompt = prompt + build_reprompt_suffix(
        invalid_commands=invalid_commands,
        errors=errors,
        allowed_command_keys=_PLANNER_ALLOWED_KEYS,
    )
    try:
        print(f"[Planner] 偵測到非法計畫，重問一次 LLM：{invalid_commands}")
        corrected = await _chat_with_retry(
            llm,
            reprompt,
            SYSTEM_PROMPT,
            reasoning_effort="high",
        )
        decision = _parse_decision_text(corrected)
        if decision.get("action") != "plan":
            return None
        correction_errors = validate_commands(
            decision.get("commands", []),
            allowed_commands=PLAN_ALLOWED_COMMANDS,
        )
        if correction_errors:
            print(f"[Planner] 修正後計畫仍不合法：{[e.command for e in correction_errors]}")
            return None
        print(f"[Planner] 修正後計畫: {decision.get('commands')}")
        return decision
    except Exception as e:
        print(f"[Planner] 修正計畫失敗: {e}")
        return None


def _stop_command_for_activity(activity: str) -> str | None:
    stop_map = {
        "fishing": "stopfish",
        "chopping": "stopchop",
        "mining": "stopmine",
        "smelting": "stopsmelt",
        "surface": "stopsurface",
        "explore": "stopexplore",
        "combat": "stopcombat",
        "hunting": "stophunt",
        "getfood": "stopgetfood",
    }
    return stop_map.get(activity)


def _distance_sq(a: dict | None, b: dict | None) -> float:
    ax = float((a or {}).get("x", 0.0))
    ay = float((a or {}).get("y", 0.0))
    az = float((a or {}).get("z", 0.0))
    bx = float((b or {}).get("x", 0.0))
    by = float((b or {}).get("y", 0.0))
    bz = float((b or {}).get("z", 0.0))
    return (ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2


def _maybe_plan_resume(message: str, state: dict) -> dict | None:
    if not any(re.search(p, message, re.IGNORECASE) for p in RESUME_PATTERNS):
        return None

    task = task_memory.load()
    resume_current = bool(task and task.get("status") == "interrupted")
    if not resume_current:
        task = task_memory.latest_interrupted()
    if not task:
        return {"command": "chat", "text": "目前沒有可恢復的中斷任務。"}

    steps = task.get("steps", [])
    current_step = task.get("currentStep", 0)
    remaining = [
        s["cmd"] for s in steps[current_step:]
        if s.get("status") not in ("done", "failed")
    ] or task.get("commands", [])[current_step:]

    if not remaining:
        return {"command": "chat", "text": "目前沒有可恢復的步驟。"}

    task_context = task.get("context") or {}
    work_pos = task_context.get("workPos") or task_context.get("currentPos")
    if not work_pos:
        for step in reversed(steps):
            sc = step.get("context") or {}
            work_pos = sc.get("workPos") or sc.get("currentPos")
            if work_pos:
                break

    commands = list(remaining)
    current_pos = state.get("pos") or {}
    if work_pos and _distance_sq(current_pos, work_pos) > 100 ** 2:
        commands = [f"tp {round(work_pos['x'])} {round(work_pos['y'])} {round(work_pos['z'])}"] + commands

    return {
        "action": "plan",
        "goal": task.get("goal", "恢復中斷任務"),
        "commands": commands,
        "resume_task": resume_current,
    }


def _is_resume_message(message: str) -> bool:
    return any(re.search(p, message, re.IGNORECASE) for p in RESUME_PATTERNS)


def _is_restart_from_here_message(message: str) -> bool:
    return any(re.search(p, message, re.IGNORECASE) for p in RESTART_FROM_HERE_PATTERNS)


def _is_mining_intent_message(message: str) -> bool:
    lowered = message.lower()
    return any(re.search(p, lowered if p.startswith(r"\b") else message, re.IGNORECASE) for p in MINING_INTENT_PATTERNS)


def _is_return_to_recent_mining_message(message: str) -> bool:
    if _is_restart_from_here_message(message):
        return False
    if not _is_mining_intent_message(message):
        return False
    return any(re.search(p, message, re.IGNORECASE) for p in RETURN_TO_MINING_PATTERNS)


def _food_summary(state: dict) -> dict:
    summary = json.loads(summary_json(state))
    return ((summary.get("resources") or {}).get("food") or {})


def _has_sufficient_food_for_resume(state: dict, threshold: int = 16) -> bool:
    food = _food_summary(state)
    cooked_total = int(food.get("cooked_total", 0) or 0)
    return cooked_total >= threshold


def _strip_food_prep_prefix(commands: list[str], keep_stop_prefix: bool = True) -> list[str]:
    if not commands:
        return []

    kept: list[str] = []
    index = 0
    if keep_stop_prefix:
        while index < len(commands):
            head = (commands[index] or "").strip()
            if not head.startswith("stop"):
                break
            kept.append(head)
            index += 1

    while index < len(commands):
        cmd = (commands[index] or "").strip()
        if not cmd:
            index += 1
            continue
        if cmd.startswith("hunt ") or cmd.startswith("getfood ") or cmd.startswith("fish "):
            index += 1
            continue
        break

    return kept + [cmd for cmd in commands[index:] if cmd]


def _task_remaining_commands(task: dict | None) -> list[str]:
    if not task:
        return []
    steps = task.get("steps", [])
    current_step = int(task.get("currentStep", 0) or 0)
    remaining = [
        s["cmd"] for s in steps[current_step:]
        if s.get("status") not in ("done", "failed")
    ] or task.get("commands", [])[current_step:]
    return list(remaining)


def _find_recent_mining_task() -> dict | None:
    current = task_memory.load_any()
    candidates: list[dict] = []
    if current:
        candidates.append(current)
    candidates.extend(task_memory.interrupted_tasks())
    for task in candidates:
        remaining = _task_remaining_commands(task)
        if any((cmd or "").split()[:1] == ["mine"] for cmd in remaining):
            return task
    return None


def _maybe_plan_restart_from_here(message: str, state: dict) -> dict | None:
    if not _is_restart_from_here_message(message):
        return None
    if not _is_mining_intent_message(message):
        return None

    task = _find_recent_mining_task()
    if not task:
        return None

    commands = _task_remaining_commands(task)
    if not commands:
        return None

    commands = [cmd for cmd in commands if not cmd.startswith("tp ")]
    if not commands:
        return None

    activity = state.get("activity", "idle")
    stop_cmd = _stop_command_for_activity(activity)
    if activity not in (None, "idle") and stop_cmd:
        commands = [stop_cmd] + commands

    return {
        "action": "plan",
        "goal": task.get("goal", "從目前位置重新開始任務"),
        "commands": normalize_commands(commands),
        "resume_task": False,
    }


def _maybe_plan_return_to_recent_mining(message: str, state: dict) -> dict | None:
    if not _is_return_to_recent_mining_message(message):
        return None

    task = _find_recent_mining_task()
    if not task:
        return None

    commands = _task_remaining_commands(task)
    if not commands:
        return None

    if _has_sufficient_food_for_resume(state, threshold=16):
        commands = _strip_food_prep_prefix(commands)

    if not commands:
        return None

    activity = state.get("activity", "idle")
    stop_cmd = _stop_command_for_activity(activity)
    if activity not in (None, "idle") and stop_cmd and commands[0] != stop_cmd:
        commands = [stop_cmd] + commands

    return {
        "action": "plan",
        "goal": task.get("goal", "恢復近期挖礦任務"),
        "commands": normalize_commands(commands),
        "resume_task": False,
    }


def _maybe_plan_come(message: str, activity: str, player_name: str | None) -> dict | None:
    lowered = message.lower()
    if not any(re.search(pattern, lowered if pattern.startswith(r"\b") else message) for pattern in COME_PATTERNS):
        return None

    commands: list[str] = []
    stop_map = {
        "fishing": "stopfish",
        "chopping": "stopchop",
        "mining": "stopmine",
        "smelting": "stopsmelt",
        "surface": "stopsurface",
        "explore": "stopexplore",
        "combat": "stopcombat",
        "hunting": "stophunt",
        "getfood": "stopgetfood",
    }
    stop_cmd = stop_map.get(activity)
    if stop_cmd:
        commands.append(stop_cmd)

    if player_name:
        commands.append(f"tp {player_name}")
    else:
        commands.append("come")

    return {"action": "plan", "commands": commands}


def _maybe_plan_surface(message: str, activity: str) -> dict | None:
    lowered = message.lower()
    if not any(re.search(pattern, lowered if pattern.startswith(r"\b") else message) for pattern in SURFACE_PATTERNS):
        return None

    commands: list[str] = []
    stop_map = {
        "fishing": "stopfish",
        "chopping": "stopchop",
        "mining": "stopmine",
        "smelting": "stopsmelt",
        "surface": "stopsurface",
        "explore": "stopexplore",
        "combat": "stopcombat",
        "hunting": "stophunt",
        "getfood": "stopgetfood",
    }
    stop_cmd = stop_map.get(activity)
    if stop_cmd:
        commands.append(stop_cmd)
    commands.append("surface")
    return {"action": "plan", "commands": commands}


def _maybe_plan_stop(message: str, activity: str) -> dict | None:
    if not any(re.search(pattern, message.lower() if pattern.startswith(r"^\s*stop") else message) for pattern in STOP_PATTERNS):
        return None
    stop_cmd = _stop_command_for_activity(activity)
    # 標記任務為中斷，讓之後說「繼續」能接回
    task_memory.interrupt("player_stop")
    if not stop_cmd:
        return {"command": "chat", "text": "已暫停。說「繼續」可接回任務。"}
    return {"action": "plan", "commands": [stop_cmd]}


_REASONING_RESOURCE_KEYWORDS = {
    "iron": ["mine iron", "smelt raw_iron", "smelt iron"],
    "diamond": ["mine diamond"],
    "wood": ["chop"],
    "log": ["chop"],
    "coal": ["mine coal"],
    "gold": ["mine gold", "smelt raw_gold"],
    "food": ["hunt", "fish"],
}

def _check_reasoning_consistency(reasoning: str, commands: list[str]) -> None:
    if not reasoning:
        return
    lowered = reasoning.lower()
    commands_str = " ".join(commands).lower()
    for keyword, expected_cmds in _REASONING_RESOURCE_KEYWORDS.items():
        if keyword in lowered:
            if not any(ec in commands_str for ec in expected_cmds):
                print(f"[Planner] ⚠ reasoning 提到 '{keyword}' 但 commands 沒有對應步驟: {commands}")


async def handle(state: dict, llm: LLMClient) -> dict | None:
    message = state.get("message", "")
    player_name = state.get("from")
    activity = state.get("activity", "idle")
    mode = state.get("mode", "survival")
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    stack = state.get("stack", [])
    chests = state.get("chests", [])

    top = stack[-1] if stack else {}
    goal = top.get("goal", {})
    progress = top.get("progress", {})
    goal_str = f"目標：{goal}，進度：{progress}" if goal else "（無目標）"

    prev_task = task_memory.load_any()
    task_ctx = ""
    remaining_cmds_for_resume = []
    if prev_task:
        task_status = prev_task.get("status", "unknown")
        steps = prev_task.get("steps", [])
        done_steps = [s["cmd"] for s in steps if s["status"] == "done"]
        remaining_cmds_for_resume = [s["cmd"] for s in steps if s["status"] not in ("done", "failed")] \
            if task_status in ("running", "interrupted") else []

        # Extract last known work position from task context
        task_context = prev_task.get("context") or {}
        work_pos = task_context.get("workPos") or task_context.get("currentPos")
        if not work_pos:
            for s in reversed(steps):
                sc = s.get("context") or {}
                work_pos = sc.get("workPos") or sc.get("currentPos")
                if work_pos:
                    break
        work_pos_str = ""
        if work_pos:
            work_pos_str = f"\n上次工作位置：({work_pos.get('x', 0):.0f}, {work_pos.get('y', 0):.0f}, {work_pos.get('z', 0):.0f})"

        final_goal = prev_task.get("final_goal")
        final_goal_str = f"\n最終目標：{final_goal}" if final_goal else ""

        _STATUS_LABEL = {
            "running":     "【執行中任務】",
            "interrupted": "【未完成任務】",
            "done":        "【前次完成任務】",
            "failed":      "【前次失敗任務】",
        }
        label = _STATUS_LABEL.get(task_status, f"【任務({task_status})】")

        pending_note = ""
        if task_status in ("running", "interrupted") and remaining_cmds_for_resume:
            pending_note = "（注意：待執行步驟的資源視為「即將取得」，規劃時不要因背包暫時沒有而說無法完成）\n"

        task_ctx = (
            f"\n\n{label}目標：{prev_task['goal']}{final_goal_str}\n"
            f"已完成步驟：{done_steps or '（無）'}\n"
            + (f"待執行步驟：{remaining_cmds_for_resume}\n{pending_note}" if remaining_cmds_for_resume else "")
            + work_pos_str
        )

    task_history = build_for_skill("planner", task_memory.recent_events(), task_memory.recent_failures(), task_memory.interrupted_tasks())
    chests_summary = build_chests_summary(chests, max_chests=4, max_items=4)

    prompt = (
        f"玩家說：「{message}」\n\n"
        f"機器人目前狀態：活動={activity}，模式={mode}，"
        f"位置=({pos.get('x',0):.0f}, {pos.get('y',0):.0f}, {pos.get('z',0):.0f})，"
        f"血量={health}/20，飢餓={food}/20。\n"
        f"當前任務：{goal_str}{task_ctx}\n\n"
        f"已登記箱子：\n{chests_summary}\n\n"
        f"{task_history}"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
        f"請根據玩家的話決定要做什麼。"
    )

    response = None
    try:
        print(f"[Planner] 玩家: {message}")

        shortcut = _maybe_plan_restart_from_here(message, state)
        if shortcut:
            print(f"[Planner] 就地重開任務快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_return_to_recent_mining(message, state)
        if shortcut:
            print(f"[Planner] 回去挖礦快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_resume(message, state)
        if shortcut:
            if shortcut.get("action") == "plan":
                print(f"[Planner] 恢復任務快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_come(message, activity, player_name)
        if shortcut:
            print(f"[Planner] 快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _equipment_goal_shortcut(message, state, activity)
        if shortcut:
            print(f"[Planner] 裝備目標快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_surface(message, activity)
        if shortcut:
            print(f"[Planner] 快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_stop(message, activity)
        if shortcut:
            print(f"[Planner] 快捷規劃: {shortcut.get('commands')}")
            return shortcut
        response = await _chat_with_retry(llm, prompt, SYSTEM_PROMPT)
        decision = _parse_decision_text(response)

        if decision.get("action") == "plan":
            commands = normalize_commands(decision.get("commands", []))
            errors = validate_commands(commands, allowed_commands=PLAN_ALLOWED_COMMANDS)
            if errors:
                repaired = await _reprompt_invalid_plan(
                    llm,
                    prompt,
                    invalid_commands=[error.command for error in errors],
                    errors=errors,
                )
                if repaired:
                    return repaired
                return _planner_failure_chat()
            decision["commands"] = commands
            reasoning = (decision.get("reasoning") or "").strip()
            _check_reasoning_consistency(reasoning, commands)
            print(f"[Planner] 計畫: {commands}")
            if reasoning:
                return [
                    {"command": "chat", "text": reasoning},
                    decision,
                ]
            return decision  # agent.py routes to executor

        if decision.get("action") == "chat":
            return {"command": "chat", "text": decision.get("text", "")}

    except Exception as e:
        print(f"[Planner] 解析失敗: {e}\n原始回應: {response!r}")

    return _planner_failure_chat()
