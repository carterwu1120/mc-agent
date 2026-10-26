from __future__ import annotations


EQUIP_WORTHY_NEXT = {"mine", "combat", "hunt"}
EQUIP_WORTHY_PREV = {"mine", "smelt", "withdraw"}
EQUIPMENT_CHANGING = {"mine", "smelt", "withdraw", "makechest"}
NON_PROGRESS = {"chat", "idle"}
SMELT_ALIASES = {
    "iron": "raw_iron",
    "iron_ingot": "raw_iron",
    "gold": "raw_gold",
    "gold_ingot": "raw_gold",
    "copper": "raw_copper",
    "copper_ingot": "raw_copper",
    "steak": "beef",
    "cooked_beef": "beef",
    "cooked_porkchop": "porkchop",
    "cooked_chicken": "chicken",
    "cooked_mutton": "mutton",
    "cooked_rabbit": "rabbit",
    "cooked_cod": "cod",
    "cooked_salmon": "salmon",
    "baked_potato": "potato",
}


def command_name(cmd: str) -> str:
    parts = (cmd or "").split()
    return parts[0] if parts else ""


def _canonicalize_command(cmd: str) -> str:
    parts = cmd.split()
    if len(parts) >= 2 and parts[0] == "smelt":
        target = SMELT_ALIASES.get(parts[1], parts[1])
        return " ".join([parts[0], target, *parts[2:]])
    return cmd


def normalize_commands(commands: list[str], previous_command: str | None = None) -> list[str]:
    normalized: list[str] = []
    last_non_chat = command_name(previous_command or "")

    for idx, raw in enumerate(commands):
        cmd = _canonicalize_command((raw or "").strip())
        if not cmd:
            continue

        name = command_name(cmd)
        next_name = ""
        for later in commands[idx + 1:]:
            later_name = command_name(later)
            if later_name and later_name not in NON_PROGRESS:
                next_name = later_name
                break

        if name == "equip":
            # Never keep repeated equip chains.
            if last_non_chat == "equip":
                continue
            # equip is meaningful either right before gear-using work,
            # or as the final "apply crafted/upgraded gear" step.
            if next_name in EQUIP_WORTHY_NEXT:
                normalized.append(cmd)
                last_non_chat = name
                continue
            if next_name:
                continue
            if last_non_chat not in EQUIP_WORTHY_PREV:
                continue

        normalized.append(cmd)
        if name not in NON_PROGRESS:
            last_non_chat = name

    return normalized


def build_step_records(commands: list[str]) -> list[dict]:
    return [{"cmd": cmd, "status": "pending", "error": None} for cmd in commands]
