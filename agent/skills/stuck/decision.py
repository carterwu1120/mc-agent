ALLOWED_ACTIVITY_STUCK_COMMANDS = {
    "chop", "mine", "chat", "idle", "home", "back", "surface", "explore", "withdraw", "fishing_decision"
}


def normalize_decision(
    activity: str,
    reason: str,
    needed_for: str | None,
    missing: list,
    missing_count: int | None,
    decision: dict,
) -> dict:
    if decision.get("command") == "chop" and "goal" not in decision and "args" not in decision:
        if activity == "smelting" and reason == "missing_dependency":
            if needed_for == "pickaxe" or any(m in ("wood", "crafting_material", "stick") for m in missing):
                decision = {**decision, "goal": {"logs": 4}}
    if activity == "smelting" and reason == "missing_dependency" and "cobblestone" in missing:
        desired_count = str(missing_count or 8)
        args = decision.get("args")
        if decision.get("command") == "mine":
            if not args:
                decision = {**decision, "args": ["stone", desired_count]}
            elif len(args) == 1:
                target = args[0]
                if target != "stone":
                    target = "stone"
                decision = {**decision, "args": [target, desired_count]}
            elif len(args) >= 2:
                target = args[0]
                count = args[1]
                if target != "stone":
                    target = "stone"
                try:
                    count_num = int(count)
                except Exception:
                    count_num = 0
                if count_num < int(desired_count):
                    count = desired_count
                decision = {**decision, "args": [target, str(count)]}
    return decision


def is_valid_decision(decision: dict) -> bool:
    command = decision.get("command")
    if command not in ALLOWED_ACTIVITY_STUCK_COMMANDS:
        return False

    if command == "mine":
        args = decision.get("args") or []
        return (
            isinstance(args, list)
            and len(args) >= 2
            and isinstance(args[0], str)
            and isinstance(args[1], str)
        )

    if command == "chop":
        goal = decision.get("goal") or {}
        args = decision.get("args") or []
        return (
            (isinstance(goal, dict) and isinstance(goal.get("logs"), int | float))
            or (isinstance(args, list) and len(args) >= 1)
        )

    if command == "withdraw":
        args = decision.get("args") or []
        return isinstance(args, list) and len(args) >= 2

    if command == "explore":
        args = decision.get("args") or []
        goal = decision.get("goal") or {}
        return (
            (isinstance(args, list) and len(args) >= 1 and isinstance(args[0], str))
            or (isinstance(goal, dict) and isinstance(goal.get("target"), str))
        )

    if command == "fishing_decision":
        action = decision.get("action")
        if action == "stop":
            return True
        if action == "move":
            return isinstance(decision.get("x"), int | float) and isinstance(decision.get("z"), int | float)
        return False

    return True
