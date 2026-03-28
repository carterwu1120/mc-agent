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

### Activity Stack (`activity.js`)

Activities are managed by a **LIFO stack**. Each frame stores:

```js
{
    activity:  'mining',                       // activity name
    goal:      { target: 'iron', count: 10 }, // original goal (immutable)
    progress:  { count: 5 },                  // updated during execution
    startPos:  { x: 100, y: -16, z: -200 },  // position at push time
    startTime: 1711411200000,                 // Date.now() at push
    resumeFn:  Function,                      // closure to restart — NOT sent to Python
}
```

**API:**
- `register(name, pauseFn)` — called once at module load, registers pause handler
- `push(bot, name, goal, resumeFn)` — auto-pauses current top, pushes new frame
- `pop(bot)` — pops top frame, calls previous frame's `resumeFn`
- `pause(bot)` — calls registered `pauseFn` for top (no pop; used by inventory)
- `resumeCurrent(bot)` — calls top frame's `resumeFn` without popping (used by inventory)
- `updateProgress(data)` — merges data into top frame's progress
- `updateTopGoal(goal)` — updates top frame's goal and resets progress (used by `_resumeX`)
- `getActivity()` — top frame name or `'idle'`
- `getStack()` — all frames without `resumeFn` (JSON-safe, sent to Python)

### Activity Module Pattern

Every activity follows this exact pattern:

```js
const activityStack = require('./activity')

let isActive = false
let _isPaused = false
let _progressVar = 0  // promoted from loop-local

activityStack.register('name', _pause)

function _pause(_bot) { isActive = false; _isPaused = true }

async function startX(bot, goal = {}) {
    if (isActive) return
    isActive = true; _progressVar = 0
    activityStack.push(bot, 'name', goal, (b) => _resumeX(b, goal))
    _loop(bot, goal)
}

function _resumeX(bot, originalGoal) {  // does NOT push — updates existing frame
    if (isActive) return
    const remaining = originalGoal.count
        ? Math.max(1, originalGoal.count - _progressVar) : undefined
    isActive = true
    activityStack.updateTopGoal(remaining ? { ...originalGoal, count: remaining } : originalGoal)
    _loop(bot, originalGoal)
}

function stopX(_bot) {
    if (!isActive) return
    isActive = false; _isPaused = false
    // loop handles the pop
}

async function _loop(bot, goal) {
    _isPaused = false
    while (isActive) {
        // on goal reached: isActive = false; bridge.sendState(done); break
        // on stuck: isActive = false; bridge.sendState(stuck); break
        // update progress: activityStack.updateProgress({ count: _progressVar })
    }
    if (!_isPaused) activityStack.pop(bot)  // pop only if not paused by another activity
    _isPaused = false
}
```

**Key rule:** `stopX` sets flags only — `_loop` calls `activityStack.pop(bot)` when it exits. `_pause` sets `_isPaused = true` so `_loop` skips the pop (stack already handled by `push`).

### Existing Activity Modules

| Module | Activity name | Progress field | Stuck reasons sent |
|--------|--------------|---------------|-------------------|
| `fishing.js` | `fishing` | `catches` | `fishing_stuck` (separate event) |
| `woodcutting.js` | `chopping` | `logs` | — |
| `mining.js` | `mining` | `count` | `no_blocks` |
| `smelting.js` | `smelting` | `smelted` | `no_input` |
| `combat.js` | `combat` | — | — |

### Inventory Interruption (Transient)

Inventory does **not** push its own frame. It's a transient interruption:

```js
// Pause current top activity:
activityStack.pause(bot)

// ... handle inventory ...

// Resume current top activity:
activityStack.resumeCurrent(bot)
```

### Event Types (JS → Python)

| Type | Meaning | Python handles? |
|------|---------|----------------|
| `tick` | Heartbeat every 2s | No |
| `activity_done` | Goal reached, bot idles | **No** — bot just waits |
| `activity_stuck` | Stuck mid-activity | **Yes** — LLM decides recovery |
| `fishing_stuck` | Fishing-specific stuck | Yes |
| `inventory_full` | Inventory full | Yes |
| `craft_decision` | Needs crafting decision | Yes |
| `food_low` | Food < 10 & idle & no food in inventory | Yes |
| `chat` | Player sent a message | No (logged only) |

**Key rule:** `activity_done` (goal reached) is NOT routed to Python. The bot idles and waits for the next command. Only `activity_stuck` triggers LLM intervention.

### Bridge State Sent to Python

Every `sendState` call includes:
- `activity` — top frame name or `'idle'` (backward compat)
- `stack` — full frame array without `resumeFn` (for LLM context)
- `pos`, `health`, `food`, `inventory`, `entities`

Example `stack` value:
```json
[
  { "activity": "mining", "goal": { "target": "iron", "count": 10 },
    "progress": { "count": 5 }, "startPos": { "x": 100, "y": -16, "z": -200 } },
  { "activity": "combat", "goal": {}, "progress": {}, "startPos": { ... } }
]
```

### Command Routing (`commands.js`)

All commands from Python arrive in `handle(bot, msg)` via WebSocket. Add new activities as a `case` in the switch. Existing commands:

`fish`, `stopfish`, `chop`, `stopchop`, `mine`, `stopmine`, `smelt`, `stopsmelt`, `combat`, `stopcombat`, `getfood`, `sethome`, `home`, `back`, `equip`, `unequip`, `come`, `look`, `tp`, `bury`, `clear`, `inv`

### Other Key Modules

| Module | Purpose |
|--------|---------|
| `bridge.js` | WebSocket server, `sendState(bot, type, extra)` |
| `activity.js` | LIFO activity stack manager |
| `water.js` | Background water escape monitor (3-phase: swim up, pathfind dry, horizontal push) |
| `eating.js` | Auto-eats when food < 18, triggered by `bot.on('health')` |
| `inventory.js` | Pauses top activity when full, asks LLM via `inventory_full` |
| `crafting.js` | `ensureToolFor`, `ensurePickaxeTier`, `applyCraftDecision` |
| `equipment.js` | `equipBestLoadout`, `equipSpecific`, `unequipAll` |
| `world.js` | Utility: `findNearestPlayer`, entity/block helpers |
| `buried.js` | Tracks positions where items were buried (avoids re-digging) |
| `home.js` | Base location: `setHome`, `goHome`, `getHome`, `back` — persisted to `agent/data/home.json`. `back` tps to top activity's `startPos` and calls `resumeCurrent`. |

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
    stack    = state.get("stack", [])        # full activity stack for context
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

`fish`, `chop`, `mine` (args: ore type), `smelt` (args: material), `combat`, `getfood`, `equip`, `come`, `home`, `back`, `chat`, `idle`

### Existing Skills

| File | Triggered by | Purpose |
|------|-------------|---------|
| `fishing.py` | `fishing_stuck` | Fishing recovery decisions |
| `inventory.py` | `inventory_full` | Drop items or compact inventory |
| `craft_decision.py` | `craft_decision` | Decide what to craft |
| `activity_stuck.py` | `activity_stuck` | Activity-specific recovery (mining/smelting), fallback for others |
| `food.py` | `food_low` | 補充食物：烤生肉 → 打動物 → 釣魚（確定性邏輯，不呼叫 LLM）|

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
4. **LIFO stack** — activities form a stack. Pushing auto-pauses the current top; popping auto-resumes the previous. No manual save/restore variables needed.
5. **Prompts live in skills** — system prompts belong in `agent/skills/`, not in JS. JS only sends state, Python decides meaning.
