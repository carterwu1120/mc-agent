"""One strategy decision per attempt; supervisor owns retries and deadlines."""
import json
from agent.handlers.command_validation import PLAN_ALLOWED_COMMANDS, validate_commands
from agent.handlers.commands_ref import command_list
from agent.handlers.knowledge import knowledge
from agent.handlers.prompts import render_prompt
from agent.handlers.state_summary import summary_json
from agent.handlers.stuck.llm_utils import parse_json_with_repair
from agent.plan_utils import normalize_commands

# These commands have completion semantics in PlanExecutor. Pure chat/idle and
# stack resume are not recovery plans: old activities have already been stopped.
ALLOWED = PLAN_ALLOWED_COMMANDS - {"resumetask", "come", "look"}
_COMMANDS = command_list(sorted(ALLOWED), include_stops=False)

# The CLI receives the system prompt on every call, so only the knowledge for the
# stuck activity is sent: (recovery.md section, bot_capabilities.md sections).
_ACTIVITY_KNOWLEDGE = {
    "mining": ("挖礦", ()),
    "chopping": ("砍樹", ()),
    "surface": ("回到地表", ("移動與位置",)),
    "fishing": ("釣魚", ("食物",)),
    "smelting": ("冶煉", ("冶煉與燃料",)),
    "hunting": ("狩獵與補食物", ("食物",)),
    "getfood": ("狩獵與補食物", ("食物",)),
    "makechest": ("箱子", ("木材與箱子",)),
    "preparetool": ("工具準備", ("鎬子與工具鏈",)),
}
_COMMAND_ACTIVITY = {"mine": "mining", "chop": "chopping", "fish": "fishing", "smelt": "smelting",
                     "hunt": "hunting", "getfood": "getfood", "surface": "surface",
                     "makechest": "makechest", "preparetool": "preparetool"}


def _stuck_activity(state: dict) -> str | None:
    if state.get("reason") == "tool_preparation_failed":
        return "preparetool"
    for key in ("activity_name", "activity"):
        if state.get(key) in _ACTIVITY_KNOWLEDGE:
            return state[key]
    # Executor timeouts arrive while idle; the running plan step names the activity.
    current = str((state.get("plan_context") or {}).get("current_cmd") or "").split()
    return _COMMAND_ACTIVITY.get(current[0]) if current else None


def system_prompt(state: dict) -> str:
    sections = ["原則"]
    if state.get("watchdog"):
        sections.append("watchdog 判定（任何活動）")
    section, capabilities = _ACTIVITY_KNOWLEDGE.get(_stuck_activity(state), (None, ()))
    if state.get("reason") == "no_tools":
        capabilities = ("鎬子與工具鏈",)
    if section:
        sections.append(section)
    text = knowledge("recovery", *sections)
    if capabilities:
        text += "\n\n" + knowledge("bot_capabilities", *capabilities)
    return render_prompt("recovery", commands=_COMMANDS, knowledge=text)


def _restore_remaining_goal(commands: list[str], plan_context: dict | None) -> list[str]:
    """A replan may add prerequisites, but must not silently drop the goal."""
    context = plan_context or {}
    if context.get('objective'):
        return commands
    current_cmd = str(context.get('current_cmd') or '').strip()
    required = ([current_cmd] if current_cmd else []) + list(context.get('pending_steps') or [])
    if not required:
        return commands
    # The model usually rewrites the remaining steps with adjusted counts. If the replan
    # already reaches the final goal step (same command and target, any count), appending
    # the old steps again would duplicate the whole chain on every recovery.
    goal_steps = [c for c in required if c.split()[0] != 'equip'] or required
    goal_key = goal_steps[-1].split()[:2]
    if any(c.split()[:2] == goal_key for c in commands):
        return commands
    if len(commands) >= len(required) and commands[-len(required):] == required:
        return commands
    for overlap in range(min(len(commands), len(required)), 0, -1):
        if commands[-overlap:] == required[:overlap]:
            return commands + required[overlap:]
    return commands + required


async def handle(state, llm):
    from agent.mining_objective import progress
    plan = dict(state.get('plan_context') or {})
    if plan.get('objective'):
        plan['objective'] = progress(plan['objective'], state)
        state = {**state, 'plan_context': plan}
    context = {k: state.get(k) for k in ("reason", "detail", "goal", "progress", "plan_context", "stack",
               "navigation", "progress_evidence", "recovery_history", "decision_error", "mining_result") if state.get(k) is not None}
    recovery_history = state.get("recovery_history") or []
    effort = "high" if len(recovery_history) >= 2 or state.get("decision_error") else "medium"
    response = await llm.chat(
        [{"role": "user", "content": summary_json(state) + "\n" + json.dumps(context, ensure_ascii=False)}],
        system=system_prompt(state),
        reasoning_effort=effort,
    )
    result = parse_json_with_repair(response)
    if not isinstance(result, dict):
        raise ValueError("decision must be an object")
    if not isinstance(result.get("text"), str) or not result["text"].strip():
        raise ValueError("decision needs a reason")
    if result.get("action") == "wait":
        return result
    commands = result.get("commands")
    if result.get("action") != "replan" or not isinstance(commands, list) or not 1 <= len(commands) <= 8:
        raise ValueError("return replan with 1-8 commands, or wait with a reason")
    errors = validate_commands(commands, allowed_commands=ALLOWED)
    if errors:
        raise ValueError(str(errors))
    done = (state.get('plan_context') or {}).get('done_steps') or []
    commands = normalize_commands(commands, previous_command=done[-1] if done else None)
    commands = _restore_remaining_goal(commands, state.get('plan_context'))
    errors = validate_commands(commands, allowed_commands=ALLOWED)
    if errors:
        raise ValueError(str(errors))
    if not commands:
        raise ValueError('replan has no executable steps after normalization')
    result['commands'] = commands
    if not isinstance(result.get("expected_outcome"), str) or not result["expected_outcome"].strip():
        raise ValueError("replan needs an observable expected_outcome")
    return result
