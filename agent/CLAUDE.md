# Minecraft Bot Agent — Architecture Guide

## Maintenance Rule

**When making architectural changes, update this file.**
If you add a new activity module, new event type, new skill handler, or change any core pattern — update the relevant section here before finishing the task.

**Never commit after making changes** unless the user explicitly asks you to commit.

## Project Scope

**Active codebase: `agent/` only.**

The following directories are legacy/unused — do NOT read or modify them:
- `fishing_tool/` — old CV-based fishing tool, replaced by mineflayer bot
- `training/` — old YOLO training scripts, no longer relevant

The entry points are:
- `agent/bot/index.js` — start the JS bot: `node agent/bot/index.js`
- `agent/agent.py` — start the Python agent: `python -m agent.agent`

---

## Architecture

Two-process system connected by WebSocket on port 3001:

```
JS Bot (mineflayer)  ←→  WebSocket :3001  ←→  Python Agent
agent/bot/                                      agent/agent.py
```

- **JS bot** — controls the Minecraft bot directly (movement, digging, fishing, combat). Runs async activity loops. Sends events to Python when something happens.
- **Python agent** — receives events, routes to skill handlers, calls LLM, sends command strings back to JS.

---

## JS Bot (`agent/bot/`)

### Activity Modules

Each activity follows this exact pattern. Use `woodcutting.js` as the canonical reference:

```js
let isActive = false

async function startX(bot, goal = {}) {
    if (isActive) return
    // pause other running activities (see combat.js or inventory.js for pause/resume pattern)
    isActive = true
    setActivity('x')        // from activity.js
    _loop(bot, goal)
}

function stopX(bot) {
    isActive = false
    setActivity('idle')
}

function isActive() { return isActive }

async function _loop(bot, goal) {
    while (isActive) {
        // on goal reached:
        bridge.sendState(bot, 'activity_done', { activity: 'x', reason: 'goal_reached' })
        break
        // on stuck mid-activity:
        bridge.sendState(bot, 'activity_stuck', { activity: 'x', reason: 'reason_code' })
        break
    }
}

module.exports = { startX, stopX, isActive }
```

### Existing Activity Modules

| Module | Activity name | Stuck reasons sent |
|--------|--------------|-------------------|
| `fishing.js` | `fishing` | `fishing_stuck` (separate event) |
| `woodcutting.js` | `chopping` | — |
| `mining.js` | `mining` | `no_blocks` |
| `smelting.js` | `smelting` | `no_input` |
| `combat.js` | `combat` | — |

### Pause / Resume Pattern

When starting a new activity that should pause others (e.g. combat, inventory handling), save and restore state:

```js
// Save
_wasFishing = isFishing(); _wasMining = isMining(); ...
if (_wasFishing) stopFishing(bot)
if (_wasMining) { _savedMiningGoal = getMiningGoal(); stopMining(bot) }

// Restore
if (_wasFishing) startFishing(bot)
if (_wasMining) startMining(bot, _savedMiningGoal)
```

See `combat.js` `startCombat`/`stopCombat` for the full implementation.

### Event Types (JS → Python)

| Type | Meaning | Python handles? |
|------|---------|----------------|
| `tick` | Heartbeat every 2s | No |
| `activity_done` | Goal reached, bot idles | **No** — bot just waits |
| `activity_stuck` | Stuck mid-activity | **Yes** — LLM decides recovery |
| `fishing_stuck` | Fishing-specific stuck | Yes |
| `inventory_full` | Inventory full | Yes |
| `craft_decision` | Needs crafting decision | Yes |
| `chat` | Player sent a message | No (logged only) |

**Key rule:** `activity_done` (goal reached) is NOT routed to Python. The bot idles and waits for the next command. Only `activity_stuck` triggers LLM intervention.

### Command Routing (`commands.js`)

All commands from Python arrive in `handle(bot, msg)` via WebSocket. Add new activities as a `case` in the switch. Existing commands:

`fish`, `stopfish`, `chop`, `stopchop`, `mine`, `stopmine`, `smelt`, `stopsmelt`, `combat`, `stopcombat`, `equip`, `unequip`, `come`, `look`, `tp`, `bury`, `clear`, `inv`

### Other Key Modules

| Module | Purpose |
|--------|---------|
| `bridge.js` | WebSocket server, `sendState(bot, type, extra)` |
| `activity.js` | Single global activity string (`setActivity`, `getActivity`) |
| `eating.js` | Auto-eats when food < 18, triggered by `bot.on('health')` |
| `inventory.js` | Pauses all activities when full, asks LLM via `inventory_full` |
| `crafting.js` | `ensureToolFor`, `ensurePickaxeTier`, `applyCraftDecision` |
| `equipment.js` | `equipBestLoadout`, `equipSpecific`, `unequipAll` |
| `world.js` | Utility: `findNearestPlayer`, entity/block helpers |
| `buried.js` | Tracks positions where items were buried (avoids re-digging) |

---

## Python Agent (`agent/`)

### Event Routing (`agent/agent.py`)

```python
HANDLERS = {
    "fishing_stuck":  fishing_skill.handle,
    "inventory_full": inventory_skill.handle,
    "craft_decision": craft_decision_skill.handle,
    "activity_stuck": activity_stuck_skill.handle,
}
```

Handlers receive `state: dict` (full bot state) and `llm: LLMClient`. Return a list of command dicts or `None`. Each command is sent back to JS and routed through `commands.js`.

`activity_done` is intentionally NOT in HANDLERS — goal completion requires no LLM decision.

### Skill Pattern (`agent/skills/`)

```python
async def handle(state: dict, llm: LLMClient) -> list | None:
    activity = state.get("activity", "unknown")
    reason   = state.get("reason", "unknown")
    inventory = state.get("inventory", [])   # list of {name, count}
    pos      = state.get("pos") or {}        # {x, y, z}
    health   = state.get("health")
    food     = state.get("food")

    # build prompt, call LLM, parse JSON response
    response = await llm.chat([{"role": "user", "content": prompt}], system=SYSTEM_PROMPT)
    decision = json.loads(clean(response))

    # return list of commands, e.g.:
    return [{"command": "chat", "text": "..."}, {"command": "mine", "args": ["iron"]}]
    # or return None to do nothing
```

### LLM Response Format

Skills instruct the LLM to return a single JSON object:
```json
{"command": "mine", "args": ["iron"], "text": "理由說明"}
```
The `text` field (if present) is sent as a chat message. `idle` means do nothing (return `None`).

### Available LLM Commands

`fish`, `chop`, `mine` (args: ore type), `smelt` (args: material), `combat`, `equip`, `come`, `chat`, `idle`

### Existing Skills

| File | Triggered by | Purpose |
|------|-------------|---------|
| `fishing.py` | `fishing_stuck` | Fishing recovery decisions |
| `inventory.py` | `inventory_full` | Drop items or compact inventory |
| `craft_decision.py` | `craft_decision` | Decide what to craft |
| `activity_stuck.py` | `activity_stuck` | Activity-specific recovery (mining/smelting), fallback for others |

### LLM Clients (`agent/brain/`)

`GeminiClient` and `OllamaClient` both implement `LLMClient`. Switch in `agent.py`:
```python
llm: LLMClient = GeminiClient()
# llm = OllamaClient(model="qwen3:14b")
```

---

## Architecture Principles

1. **Activities are tools** — each module is a dumb executor. It runs, finishes or gets stuck, then reports. It never decides what comes next.
2. **goal_reached → idle** — bot stops and waits. No automatic chaining to the next activity. Next command comes from a player or external orchestration.
3. **stuck → LLM** — only when stuck mid-activity does the LLM intervene to decide recovery.
4. **One activity at a time** — starting a new activity pauses the current one. Stopping restores it.
5. **Prompts live in skills** — system prompts belong in `agent/skills/`, not in JS. JS only sends state, Python decides meaning.
