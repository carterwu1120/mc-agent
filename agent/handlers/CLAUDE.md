# Python Event Handlers Development Guide

For architecture overview and handler file list, see agent/CLAUDE.md

---

## Handler Pattern

```python
import json
import re
from agent.brain import LLMClient
from agent.handlers.commands_ref import command_list
from agent.handlers.knowledge import knowledge
from agent.handlers.prompts import render_prompt
from agent.handlers.state_summary import summary_json, equipment_summary

_MY_COMMANDS = command_list(["mine", "chop", "chat", "idle"])

# Text lives in agent/prompts/my_handler.md with $commands / $knowledge placeholders.
SYSTEM_PROMPT = render_prompt("my_handler", commands=_MY_COMMANDS, knowledge=knowledge("recovery", "挖礦"))

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
- System prompt text lives in `agent/prompts/<handler>.md` (see its README), not in Python.
  Handlers only choose and fill placeholders; user prompts built from live state stay in code.
- Domain rules (strategies, quantities, what commands can/cannot do) go in `agent/knowledge/*.md`
  and are loaded with `knowledge(name, *sections)` from `knowledge.py`. Templates keep only the
  output format and Python-specific mechanics (plan_context, pending steps, placeholders).
  If a prompt recommends a command, that command must be in the handler's allowlist.
- `equipment` items arrive as `{name, durability_pct}` dicts — use `_item_name(item)` / `_item_durability_pct(item)` from `state_summary.py`

---

## Supervised recovery (live event handler)

`recovery.py` is the live activity_stuck strategy handler. `agent/recovery.py` owns
stop acknowledgement, concurrency, timeouts, duplicate-strategy checks and retry
budgets. The handler makes one CLI call per attempt. It uses commands_ref and
command_validation; normalizing to an empty plan is an error, not completion.
The response is either replan with 1-8 commands, text and expected_outcome, or wait
with a concrete reason. Chat-only/idle/skip do not settle a recovery. Current state,
original goal/pending steps, navigation failures, progress samples and bounded recent
attempts are supplied. An explicit wait is persisted and communicated to the player.

`expected_outcome` is descriptive context, not an arbitrary executable assertion.
Actual step/goal verification stays in PlanExecutor. No inference is made on every
heartbeat. Timeout cancels the CLI subprocess through its existing cancellation
cleanup. Tests use fake model/transport responses, never paid inference.

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


`task_memory` schema (`BOT_DATA_DIR/task.json`) — the current task only, keys in this order:
```json
{
  "id": "abc12345",
  "goal": "幫我挖鑽石",
  "status": "running",
  "source": "player",
  "createdAt": "2026-10-26T08:00:00",
  "currentStep": 1,
  "steps": [
    {"cmd": "preparetool iron_pickaxe", "status": "done"},
    {"cmd": "mine diamond 10",          "status": "running"}
  ],
  "context": {"workPos": {"x": -153, "y": -58, "z": 118}, "objective": {"kind": "mine", "count": 10}},
  "runtime": {"supervision": {"phase": "idle"}}
}
```
Step status: `pending` / `running` / `interrupted` / `done` / `failed` (failed steps add `error`).
Resume skips `done` steps. Optional keys appear only when set: `final_goal`, `interruptedBy`,
and on completion `goalVerified` / `goal_count` / `actual_count`. `context` holds where the work
is (`workPos`) and the mining `objective`; `runtime` holds live coordination state (recovery
`supervision`, food-wait snapshot) and is overwritten. The live activity/stack/position is in
`live_state.json`, not here. Events, failures and finished tasks go to the database only; the
file keeps no history, and loading an older file drops its obsolete keys.
Player chat 「清除任務」/「取消任務」/`cancel task` stops the activity and deletes task.json,
so 「繼續」 has nothing to resume.

---

## Operating Modes

The bot never starts work on its own: idle ticks only drive liveness and supervision,
and interrupted tasks resume only when the player asks. `setmode` remains for manual use.

---

## LLM Clients (`agent/brain/`)

```python
from agent.brain import LLMClient, build_client

llm: LLMClient = build_client()  # LLM_PROVIDER=codex (default) or claude
```

Both CLI providers implement `async def chat(messages, system=None) -> str`.
They use subscription login, with no direct model API fallback or API SDK dependency.
