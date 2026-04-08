# Python Skills Development Guide

For architecture overview and skill file list, see agent/CLAUDE.md

---

## Skill Pattern

```python
import json
import re
from agent.brain import LLMClient
from agent.skills.commands_ref import command_list
from agent.skills.state_summary import summary_json, equipment_summary

_MY_COMMANDS = command_list(["mine", "chop", "chat", "idle"])

SYSTEM_PROMPT = f"""...
{_MY_COMMANDS}
..."""

async def handle(state: dict, llm: LLMClient) -> list | dict | None:
    activity  = state.get("activity", "idle")
    reason    = state.get("reason", "unknown")
    stack     = state.get("stack", [])
    inventory = state.get("inventory", [])
    chests    = state.get("chests", [])
    pos       = state.get("pos") or {}
    health    = state.get("health")
    food      = state.get("food")
    equipment = state.get("equipment") or {}

    # build prompt, call LLM, parse JSON
    response = await llm.chat([{"role": "user", "content": prompt}], system=SYSTEM_PROMPT)
    clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
    decision = json.loads(clean)

    # return options:
    return [{"command": "mine", "args": ["iron"]}]          # list of commands
    return {"action": "plan", "goal": "...", "commands": []} # routes to PlanExecutor
    return None                                              # do nothing
```

**Rules:**
- Always use `command_list(keys)` from `commands_ref.py` — never write command descriptions inline
- `equipment` items arrive as `{name, durability_pct}` dicts — use `_item_name(item)` / `_item_durability_pct(item)` from `state_summary.py`

---

## LLM Response Formats

**Single command:**
```json
{"command": "mine", "args": ["iron"], "text": "optional chat message"}
```

**Plan** (routes to PlanExecutor):
```json
{"action": "plan", "goal": "簡短目標描述", "commands": ["equip", "mine iron 10"]}
```

**Replan** (only valid from `activity_stuck` during executor run):
```json
{"action": "replan", "commands": ["chop logs 20", "mine iron 10"]}
```

**Idle** → return `None`

If `text` field is present in any response, it is sent as a chat message to the player.

---

## PlanExecutor & task_memory

`PlanExecutor` sequences plan commands, waiting for `action_done` / `activity_done` between steps.

Runtime context substitution: `{new_chest_id}` in command strings is replaced after `makechest` succeeds (executor stores `new_chest_id` from `action_done` state into `_context`).

`task_memory` schema (`agent/data/task.json`):
```json
{
  "id": "abc12345",
  "goal": "幫我挖鑽石",
  "commands": ["equip", "mine diamond 10"],
  "steps": [
    {"cmd": "equip",          "status": "done",    "error": null},
    {"cmd": "mine diamond 10","status": "running", "error": null}
  ],
  "currentStep": 1,
  "status": "running",
  "interruptedBy": null
}
```
Step status: `pending` / `running` / `done` / `failed`. Resume skips `done` steps.

---

## Chest + Makechest Flow

When a skill plans chest operations:
```python
["makechest", "labelchest {new_chest_id} ore", "deposit {new_chest_id}"]
```
`{new_chest_id}` is a runtime placeholder — executor substitutes it after `makechest` sends `action_done` with `new_chest_id`. **Never replace with a literal number in the plan.**

---

## Operating Modes

| Mode | `self_task` behaviour |
|------|-----------------------|
| `companion` | Does not run |
| `survival` | Runs — handles food/tool shortages |
| `workflow` | Runs + auto-resumes interrupted tasks on idle |

Switch: `!setmode <mode>` or LLM sends `setmode <mode>` command.

---

## LLM Clients (`agent/brain/`)

```python
from agent.brain import LLMClient, GeminiClient, OllamaClient

llm: LLMClient = GeminiClient()
# llm = OllamaClient(model="qwen3:14b")
```

Both implement `async def chat(messages, system=None) -> str`.
