from __future__ import annotations

from agent.skills.state_summary import summarize_state


_LAST_ATTEMPTS: dict[tuple[str, str, str], dict] = {}


def _bot_key(state: dict) -> str:
    return str(state.get("bot_id") or state.get("username") or state.get("bot") or "default")


def _resource_fingerprint(state: dict) -> tuple:
    summary = summarize_state(state)
    resources = summary.get("resources") or {}
    wood = resources.get("wood") or {}
    materials = resources.get("materials") or {}
    utility = resources.get("utility") or {}
    tools = resources.get("tools") or {}
    return (
        int(wood.get("logs", 0) or 0),
        int(wood.get("planks", 0) or 0),
        int(wood.get("sticks", 0) or 0),
        int(materials.get("cobblestone", 0) or 0),
        int(materials.get("iron_ingot", 0) or 0),
        int(materials.get("raw_iron", 0) or 0),
        int(materials.get("diamond", 0) or 0),
        int(utility.get("crafting_table", 0) or 0),
        tuple(tools.get("pickaxe") or []),
        tuple(tools.get("axe") or []),
        tuple(tools.get("sword") or []),
    )


def should_retry(tool_kind: str, activity: str, state: dict, commands: list[str]) -> bool:
    """Return False when the same acquisition fix already failed with unchanged resources."""
    key = (_bot_key(state), activity, tool_kind)
    fingerprint = _resource_fingerprint(state)
    previous = _LAST_ATTEMPTS.get(key)
    if previous and previous.get("fingerprint") == fingerprint and previous.get("commands") == commands:
        print(f"[ToolAcquisition] suppress repeated {activity}/{tool_kind} retry; resources unchanged")
        return False
    _LAST_ATTEMPTS[key] = {"fingerprint": fingerprint, "commands": list(commands)}
    return True


def pickaxe_replan_commands(state: dict, plan_context: dict | None) -> list[str] | None:
    if state.get("craft_issue_suspected") or not plan_context:
        return None

    caps = state.get("capabilities") or {}
    pending_steps = list(plan_context.get("pending_steps") or [])
    current_cmd = (plan_context.get("current_cmd") or "").strip()

    if caps.get("can_make_pickaxe"):
        commands = ["equip"]
    else:
        commands = ["chop logs 3", "equip", "mine stone 3", "equip"]

    if current_cmd:
        commands.append(current_cmd)
    commands.extend(pending_steps)
    return commands

