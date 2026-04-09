from __future__ import annotations

from dataclasses import dataclass
import re

from agent.skills.commands_ref import COMMANDS, command_list


PLAN_ALLOWED_COMMANDS = set(COMMANDS) - {"chat", "idle", "setmode"}

_INT_RE = re.compile(r"^\d+$")
_CHEST_ID_RE = re.compile(r"^(?:\d+|\{[^{}]+\}|\{\{[^{}]+\}\})$")
_COORD_RE = re.compile(r"^-?\d+(\.\d+)?$")


@dataclass
class CommandValidationError:
    command: str
    reason: str


def _is_positive_int(value: str) -> bool:
    return bool(_INT_RE.fullmatch(value)) and int(value) > 0


def _is_coord(value: str) -> bool:
    return bool(_COORD_RE.fullmatch(value))


def _is_chest_id_token(value: str) -> bool:
    return bool(_CHEST_ID_RE.fullmatch(value))


def validate_command(command: str, allowed_commands: set[str] | None = None) -> str | None:
    if not isinstance(command, str) or not command.strip():
        return "指令必須是非空字串"

    parts = command.split()
    name = parts[0]
    args = parts[1:]
    allowed = allowed_commands or PLAN_ALLOWED_COMMANDS

    if name not in allowed:
        return f"不支援的指令 `{name}`"

    if name == "tp":
        if len(args) == 3 and all(_is_coord(a) for a in args):
            return None
        return "`tp` 格式應為 `tp <x> <y> <z>`"

    if name in {"combat", "surface", "home", "back", "equip", "makechest"}:
        return None if not args else f"`{name}` 不應帶參數"

    if name.startswith("stop"):
        return None if not args else f"`{name}` 不應帶參數"

    if name == "come":
        return None if len(args) <= 1 else "`come` 最多只能有一個玩家名稱"

    if name == "explore":
        return None if len(args) == 1 else "`explore` 格式應為 `explore <target>`"

    if name == "mine":
        if len(args) != 2 or not _is_positive_int(args[1]):
            return "`mine` 格式應為 `mine <ore> <count>`，且 count 必須是正整數"
        return None

    if name == "chop":
        if len(args) != 2 or args[0] != "logs" or not _is_positive_int(args[1]):
            return "`chop` 格式應為 `chop logs <count>`"
        return None

    if name == "fish":
        if len(args) != 2 or args[0] != "catches" or not _is_positive_int(args[1]):
            return "`fish` 格式應為 `fish catches <count>`"
        return None

    if name == "smelt":
        if len(args) != 2 or not _is_positive_int(args[1]):
            return "`smelt` 格式應為 `smelt <material> <count>`，且 count 必須是正整數"
        return None

    if name in {"hunt", "getfood"}:
        if not args:
            return None
        if len(args) == 2 and args[0] == "count" and _is_positive_int(args[1]):
            return None
        return f"`{name}` 格式應為 `{name}` 或 `{name} count <n>`"

    if name == "deposit":
        if len(args) != 1 or not _is_chest_id_token(args[0]):
            return "`deposit` 格式應為 `deposit <chest_id>`"
        return None

    if name == "withdraw":
        if len(args) == 2 and _is_chest_id_token(args[1]):
            return None
        if len(args) == 3 and _is_positive_int(args[1]) and _is_chest_id_token(args[2]):
            return None
        return "`withdraw` 格式應為 `withdraw <item> [count] <chest_id>`"

    if name == "labelchest":
        if len(args) != 2 or not _is_chest_id_token(args[0]):
            return "`labelchest` 格式應為 `labelchest <chest_id> <label>`"
        return None

    return None


def validate_commands(commands: list[str], allowed_commands: set[str] | None = None) -> list[CommandValidationError]:
    if not isinstance(commands, list) or not commands:
        return [CommandValidationError(command=str(commands), reason="commands 必須是非空陣列")]

    errors: list[CommandValidationError] = []
    for command in commands:
        reason = validate_command(command, allowed_commands=allowed_commands)
        if reason:
            errors.append(CommandValidationError(command=str(command), reason=reason))
    return errors


def format_validation_errors(errors: list[CommandValidationError]) -> str:
    return "\n".join(f"- `{error.command}`: {error.reason}" for error in errors)


def build_reprompt_suffix(
    invalid_commands: list[str],
    errors: list[CommandValidationError],
    allowed_command_keys: list[str] | set[str],
) -> str:
    ordered_keys = [key for key in COMMANDS if key in set(allowed_command_keys)]
    legal_commands = command_list(ordered_keys)
    invalid_list = "\n".join(f"- {command}" for command in invalid_commands)
    return (
        "\n\n你上一個回覆的 commands 不合法，請完整重寫一次 JSON。\n"
        "以下是上一版不合法的指令：\n"
        f"{invalid_list}\n\n"
        "錯誤原因：\n"
        f"{format_validation_errors(errors)}\n\n"
        "你只能使用以下合法指令重新輸出完整 commands，不能沿用任何非法指令：\n"
        f"{legal_commands}\n"
        "請只輸出合法 JSON，不要加任何說明文字。"
    )
