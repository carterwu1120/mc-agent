import re

from agent.brain import LLMClient
from agent.context_builder import build_for_skill
from agent.skills.llm_response import parse_llm_json
from agent import task_memory as _task_memory
from agent.skills.command_validation import PLAN_ALLOWED_COMMANDS, validate_commands
from agent.skills.stuck import decision as decision_utils
from agent.skills.stuck import getfood as getfood_stuck
from agent.skills.stuck import hunting as hunting_stuck
from agent.skills.stuck import llm_utils
from agent.skills.stuck import mining as mining_stuck
from agent.skills.stuck import prompt_builder
from agent.skills.stuck import prompts as stuck_prompts
from agent.skills.stuck import smelting as smelting_stuck

ALLOWED_COMMANDS = decision_utils.ALLOWED_ACTIVITY_STUCK_COMMANDS

# ── Rule table: (child_activity, parent_activity) pairs where skip is forbidden ──
# Skipping the child would make the parent's goal impossible to achieve.
# Add pairs here when production logs reveal a skip actually broke a task.
_CRITICAL_DEPENDENCY_PAIRS: frozenset[tuple[str, str]] = frozenset()


def _compute_is_critical_subtask(activity: str, state: dict) -> bool:
    """True if skipping this activity would break the parent activity's goal."""
    stack = state.get("stack") or []
    parent_activities = [f.get("activity") for f in stack[:-1]]
    return any((activity, p) in _CRITICAL_DEPENDENCY_PAIRS for p in parent_activities)


def _enforce_pending_steps(decision: dict, plan_context: dict | None) -> dict:
    """Ensure replan commands include pending_steps from plan_context.
    If the LLM omitted them, append them automatically."""
    if not plan_context:
        return decision
    pending_steps = plan_context.get("pending_steps") or []
    if not pending_steps:
        return decision
    commands = list(decision.get("commands") or [])
    # Already ends with pending_steps — nothing to do
    if len(commands) >= len(pending_steps) and commands[-len(pending_steps):] == pending_steps:
        return decision
    # Strip any trailing overlap (partial suffix already included)
    for overlap in range(min(len(commands), len(pending_steps)), 0, -1):
        if commands[-overlap:] == pending_steps[:overlap]:
            commands = commands + pending_steps[overlap:]
            print(f"[Stuck/Rule] replan 部分包含 pending_steps，補上剩餘 {pending_steps[overlap:]}")
            return {**decision, "commands": commands}
    print(f"[Stuck/Rule] replan 缺少 pending_steps，自動補上: {pending_steps}")
    return {**decision, "commands": commands + pending_steps}


def _build_skip_blocked_response(
    activity: str, state: dict, parent: str, parent_frame: dict | None
) -> dict:
    """Build the blocked-skip response for a specific (activity, parent) pair."""
    parent_goal = (parent_frame or {}).get("goal") or {}
    if activity == "smelting":
        target = parent_goal.get("target", "diamond")
        count = parent_goal.get("count", 10)
        smelt_item = state.get("smelt_item") or state.get("item") or "iron_ore"
        smelt_count = state.get("smelt_count") or state.get("count") or 3
        return {
            "reason": f"smelting 是 {parent} 的必要前置步驟，不能 skip",
            "fallback": {
                "action": "replan",
                "commands": [f"smelt {smelt_item} {smelt_count}", f"mine {target} {count}"],
                "text": "冶煉是採礦的必要前置，不能跳過，改為重試",
            },
        }
    # Generic fallback for other pairs: replan back to parent goal
    target = parent_goal.get("target", "")
    count = parent_goal.get("count", "")
    fallback_cmd = f"{parent} {target} {count}".strip()
    return {
        "reason": f"{activity} 是 {parent} 的必要前置步驟，不能 skip",
        "fallback": {
            "action": "replan",
            "commands": [fallback_cmd] if fallback_cmd != parent else [],
            "text": f"不能跳過 {activity}，改為重試 {parent}",
        },
    }


def _block_invalid_skip(activity: str, state: dict) -> dict | None:
    """Block skip when activity is a necessary sub-task that cannot be safely skipped.
    Driven by _CRITICAL_DEPENDENCY_PAIRS — add new pairs there to extend coverage.
    Returns None if skip is allowed, or {'reason': ..., 'fallback': decision} if blocked."""
    stack = state.get("stack") or []
    for parent_frame in reversed(stack[:-1]):
        parent = parent_frame.get("activity")
        if parent and (activity, parent) in _CRITICAL_DEPENDENCY_PAIRS:
            return _build_skip_blocked_response(activity, state, parent, parent_frame)
    return None


def _filter_done_steps_from_replan(decision: dict, plan_context: dict | None) -> dict:
    """Remove leading replan commands that duplicate already-completed steps.
    LLMs sometimes regenerate the full plan from scratch, including done steps."""
    done_steps = list((plan_context or {}).get("done_steps") or [])
    if not done_steps:
        return decision
    commands = list(decision.get("commands") or [])
    done_set = set(done_steps)
    i = 0
    while i < len(commands) and commands[i] in done_set:
        i += 1
    if i:
        print(f"[Stuck/Rule] replan 開頭重複 {i} 個已完成步驟，已移除: {commands[:i]}")
        return {**decision, "commands": commands[i:]}
    return decision


def _deduplicate_adjacent_cmds(decision: dict) -> dict:
    """Remove consecutive identical commands (e.g. ['equip', 'equip', 'mine ...'])."""
    commands = list(decision.get("commands") or [])
    deduped = [cmd for i, cmd in enumerate(commands) if i == 0 or cmd != commands[i - 1]]
    if len(deduped) != len(commands):
        print(f"[Stuck/Rule] replan 移除 {len(commands) - len(deduped)} 個連續重複指令")
        return {**decision, "commands": deduped}
    return decision


def _apply_replan_pipeline(decision: dict, plan_context: dict | None) -> dict:
    """Apply all post-LLM replan validation rules in order."""
    decision = _enforce_pending_steps(decision, plan_context)
    decision = _filter_done_steps_from_replan(decision, plan_context)
    decision = _deduplicate_adjacent_cmds(decision)
    return decision


def _should_prefer_replan(activity: str, reason: str, plan_context: dict | None) -> bool:
    if activity == "mining":
        return mining_stuck.should_prefer_replan(reason, plan_context)
    return False


def _looks_like_getfood_subflow(activity: str, reason: str, plan_context: dict | None) -> bool:
    if activity != "smelting":
        return False
    return smelting_stuck.looks_like_getfood_subflow(reason, plan_context)


def _build_getfood_replan_from_smelting(state: dict, plan_context: dict) -> list[dict] | None:
    return getfood_stuck.build_replan_from_smelting(state, plan_context)


def _build_hunting_replan_no_animals(state: dict, plan_context: dict) -> list[dict] | None:
    return hunting_stuck.build_replan_no_animals(state, plan_context)


def _recent_hunting_no_animals(state: dict) -> bool:
    return getfood_stuck.recent_hunting_no_animals(state)


def _build_getfood_replan_after_failed_hunt(state: dict, plan_context: dict) -> list[dict] | None:
    return getfood_stuck.build_replan_after_failed_hunt(state, plan_context)


async def handle(state: dict, llm: LLMClient) -> dict | None:
    activity = state.get("activity_name", state.get("activity", "unknown"))
    reason = state.get("reason", "unknown")
    inventory = state.get("inventory", [])
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    y = round(pos.get("y", 0))
    missing = state.get("missing", [])
    needed_for = state.get("needed_for")
    suggested_actions = state.get("suggested_actions", [])
    detail = state.get("detail")
    missing_count = state.get("missing_count")
    plan_context = state.get("plan_context")

    # ── Layer 1: Pre-LLM state enrichment ─────────────────────────────────────
    is_critical = _compute_is_critical_subtask(activity, state)
    if is_critical:
        state = {**state, "is_critical_subtask": True}

    if _looks_like_getfood_subflow(activity, reason, plan_context):
        shortcut = smelting_stuck.deterministic_shortcut(state, plan_context, _build_getfood_replan_from_smelting)
        if shortcut:
            return shortcut

    if activity == "hunting" and reason == "no_weapon":
        shortcut = hunting_stuck.deterministic_shortcut_no_weapon(state, plan_context)
        if shortcut:
            print("[Skill/activity_stuck] hunting/no_weapon 走 deterministic shortcut")
            return shortcut

    if activity == "hunting" and reason == "no_animals" and plan_context:
        shortcut = _build_hunting_replan_no_animals(state, plan_context)
        if shortcut:
            print("[Skill/activity_stuck] hunting/no_animals，直接改走換區域或改釣魚的 replan")
            return shortcut

    if activity == "getfood" and reason == "no_raw_food" and plan_context and _recent_hunting_no_animals(state):
        shortcut = _build_getfood_replan_after_failed_hunt(state, plan_context)
        if shortcut:
            print("[Skill/activity_stuck] getfood/no_raw_food 且最近剛 hunting/no_animals，直接改走換區域或改釣魚的 replan")
            return shortcut

    if activity == "getfood" and reason == "no_raw_food":
        shortcut = getfood_stuck.deterministic_shortcut_no_raw_food_satisfied(state, plan_context)
        if shortcut:
            print("[Skill/activity_stuck] getfood/no_raw_food 但熟食已足夠，直接跳過補食流程")
            return shortcut

    if activity == "chopping" and reason == "no_trees":
        nearby = state.get("nearby") or {}
        if not nearby.get("trees"):
            pending_steps = (plan_context or {}).get("pending_steps", [])
            if y < 40:
                commands = ["surface", "explore trees"] + pending_steps
                msg = "附近沒有樹且目前在地底，先回到地表再尋找新的樹木區域。"
            else:
                commands = ["explore trees"] + pending_steps
                msg = "附近已沒有樹，移動到新的區域尋找樹木。"
            print(f"[Skill/activity_stuck] chopping/no_trees deterministic: {commands}")
            return [
                {"command": "chat", "text": msg},
                {"action": "replan", "commands": commands},
            ]

    if activity == "mining" and reason == "no_tools":
        shortcut = mining_stuck.deterministic_shortcut(state, plan_context)
        if shortcut:
            return shortcut

    if activity == "makechest":
        chests = state.get("chests") or []
        usable = [c for c in chests if (c.get("freeSlots") or 0) > 0]
        if usable:
            chest = usable[0]
            pending_steps = (plan_context or {}).get("pending_steps", [])
            new_cmds = [f"deposit {chest['id']}"] + pending_steps
            print(f"[Skill/activity_stuck] makechest 失敗但有現有箱子 id={chest['id']}，直接 deposit")
            return [
                {"command": "chat", "text": f"改用已有箱子 id={chest['id']} 整理背包"},
                {"action": "replan", "commands": new_cmds},
            ]

    if activity == "getfood" and reason == "has_raw_food":
        raw_food = state.get("raw_food")
        raw_count = state.get("raw_count", 1)
        remaining_food = state.get("remaining", raw_count)
        pending_steps = (plan_context or {}).get("pending_steps", [])
        after_smelt = max(0, remaining_food - raw_count)
        new_cmds = [f"smelt {raw_food} {raw_count}"]
        if after_smelt > 0:
            new_cmds.append(f"getfood count {after_smelt}")
        new_cmds.extend(pending_steps)
        print(f"[Skill/activity_stuck] getfood has_raw_food → deterministic replan: {new_cmds}")
        return [
            {"command": "chat", "text": f"冶煉 {raw_food} x{raw_count}，完成後繼續補充食物"},
            {"action": "replan", "commands": new_cmds},
        ]

    if activity == "fishing":
        prompt = prompt_builder.build_fishing_prompt(state, health, food)
        pending_steps = list((plan_context or {}).get("pending_steps") or [])
    else:
        prompt, pending_steps = prompt_builder.build_activity_prompt(
            state=state,
            activity=activity,
            reason=reason,
            inventory=inventory,
            health=health,
            food=food,
            y=y,
            missing=missing,
            needed_for=needed_for,
            suggested_actions=suggested_actions,
            detail=detail,
            missing_count=missing_count,
            plan_context=plan_context,
        )

    task_history = build_for_skill(
        "activity_stuck",
        _task_memory.recent_events(),
        _task_memory.recent_failures(),
        _task_memory.interrupted_tasks(),
    )
    if task_history:
        prompt = prompt.replace("請決定機器人接下來要做什麼。", task_history + "\n請決定機器人接下來要做什麼。")

    system = stuck_prompts.SYSTEM_PROMPTS.get(activity, stuck_prompts.SYSTEM_PROMPT_FALLBACK)
    if plan_context:
        system = system + stuck_prompts.PLAN_CONTEXT_SUFFIX

    response = None
    try:
        print(f"[Skill/activity_stuck] Prompt:\n{prompt}\n---")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=system,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = parse_llm_json(llm_utils.parse_json_with_repair(clean), "Skill/activity_stuck")

        if _should_prefer_replan(activity, reason, plan_context) and decision.get("action") not in {"replan", "skip"}:
            repaired = await llm_utils.reprompt_for_replan_strategy(
                llm,
                prompt,
                system,
                decision,
                pending_steps,
            )
            if not repaired:
                return llm_utils.replan_fallback("我需要先重新規劃剩餘步驟，這次先跳過目前卡住的修復。")
            decision = repaired

        if decision.get("action") == "replan" and decision.get("commands"):
            errors = validate_commands(
                decision.get("commands", []),
                allowed_commands=PLAN_ALLOWED_COMMANDS,
            )
            if errors:
                repaired = await llm_utils.reprompt_invalid_replan(
                    llm,
                    prompt,
                    system,
                    invalid_commands=[error.command for error in errors],
                    errors=errors,
                )
                if not repaired:
                    return llm_utils.replan_fallback("我剛剛重新規劃失敗，先跳過這一步繼續。")
                decision = repaired
            # ── Deterministic rules pipeline ───────────────────────────────
            decision = _apply_replan_pipeline(decision, plan_context)
            result = []
            if decision.get("text"):
                result.append({"command": "chat", "text": decision["text"]})
            result.append({"action": "replan", "commands": decision["commands"]})
            return result

        if decision.get("action") == "skip":
            # ── Deterministic rule: 不可在必要的子任務 skip ─────────────────
            blocked = _block_invalid_skip(activity, state)
            if blocked:
                print(f"[Skill/activity_stuck] skip 被系統層阻擋: {blocked['reason']}")
                decision = blocked["fallback"]
                if decision.get("action") == "replan":
                    decision = _apply_replan_pipeline(decision, plan_context)
                    result = []
                    if decision.get("text"):
                        result.append({"command": "chat", "text": decision["text"]})
                    result.append({"action": "replan", "commands": decision["commands"]})
                    return result
            result = []
            if decision.get("text"):
                result.append({"command": "chat", "text": decision["text"]})
            result.append({"action": "skip"})
            return result

        decision = decision_utils.normalize_decision(activity, reason, needed_for, missing, missing_count, decision)
        if not decision_utils.is_valid_decision(decision):
            print(f"[Skill/activity_stuck] 無效 decision，忽略: {decision}")
            return None

        text = decision.get("text", "").strip()
        command = decision.get("command")
        result = []
        if command == "chat":
            if text:
                result.append({"command": "chat", "text": text})
            return result or None
        if text:
            result.append({"command": "chat", "text": text})
        if command != "idle":
            cmd = {k: v for k, v in decision.items() if k != "text"}
            result.append(cmd)
        return result if result else None
    except Exception as e:
        print(f"[Skill/activity_stuck] 解析失敗: {e}\n原始回應: {response!r}")
        if plan_context:
            return llm_utils.replan_fallback("我剛剛重新規劃失敗，先跳過這一步繼續。")
        return None
