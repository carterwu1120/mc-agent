import re

from agent.brain import LLMClient
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
        decision = llm_utils.parse_json_with_repair(clean)

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
            result = []
            if decision.get("text"):
                result.append({"command": "chat", "text": decision["text"]})
            result.append({"action": "replan", "commands": decision["commands"]})
            return result

        if decision.get("action") == "skip":
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
