"""Generate references/commands.md from the canonical registry in agent/skills/commands_ref.py."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Load the file directly: importing the agent.skills package pulls in the whole agent runtime.
_spec = importlib.util.spec_from_file_location("commands_ref", ROOT / "agent" / "skills" / "commands_ref.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
COMMANDS = _module.COMMANDS

# Python-orchestration commands that mean nothing to the skill.
EXCLUDED = {"setmode"}
# Bot commands handled by bot/commands.js that the Python registry does not list.
EXTRA = {
    "sethome":   {"desc": "把目前位置設為基地", "usage": "sethome"},
    "stopall":   {"desc": "停止所有活動並清空活動堆疊（stuck 後必做）", "usage": "stopall"},
    "unequip":   {"desc": "卸下裝備", "usage": "unequip [item]"},
    "setchest":  {"desc": "登記附近箱子", "usage": "setchest"},
    "readchest": {"desc": "重新讀取箱子內容", "usage": "readchest [chest_id]"},
    "look":      {"desc": "看向玩家或最近實體", "usage": "look [player]"},
}


def render() -> str:
    lines = [
        "# Bot commands",
        "",
        "Generated from `agent/skills/commands_ref.py`. Descriptions are in Traditional Chinese.",
        "Run with `mcctl run <usage>`. `chat <text>` speaks in game, `idle` is a no-op.",
        "Counts must be explicit positive integers.",
        "",
        "| usage | description | example |",
        "|---|---|---|",
    ]
    for key, entry in {**COMMANDS, **EXTRA}.items():
        if key in EXCLUDED:
            continue
        usage = "chat <text>" if key == "chat" else entry["usage"]
        lines.append(f"| `{usage}` | {entry['desc']} | {entry.get('example', '')} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    target = Path(sys.argv[1])
    target.write_text(render(), encoding="utf-8")
    print(f"wrote {target}")
