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
| `fishing.js` | `fishing` | `catches` | `bad_cast` via `activity_stuck` |
| `woodcutting.js` | `chopping` | `logs` | — |
| `mining.js` | `mining` | `count` | `no_blocks` |
| `smelting.js` | `smelting` | `smelted` | `no_input`, `missing_dependency` |
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
| `tick` | Heartbeat every 2s | Yes — self_task (idle only, 60s cooldown) |
| `activity_done` | Goal reached, bot idles | Yes — signals PlanExecutor + marks task done |
| `action_done` | Instant action completed | Yes — signals PlanExecutor |
| `task_started` | Activity started (via `!` command or LLM) | Yes — saves to task_memory for resume |
| `task_stopped` | Activity manually stopped | Yes — marks task_memory interrupted |
| `activity_stuck` | Stuck mid-activity | Yes — LLM decides recovery |
| `inventory_full` | Inventory full | Yes — LLM decides drop/plan |
| `craft_decision` | Needs crafting decision | Yes |
| `food_low` | Food < 10 & idle & no food in inventory | Yes — deterministic, no LLM |
| `chat` | Player sent a message | Yes — planner skill (natural language → commands) |
| `player_died` | Bot health → 0 (detected via `bot.on('health')`) | Yes — aborts executor, saves death info to `agent/data/death.json` |
| `player_respawned` | Bot respawned (registered inside `bot.once('spawn')`) | Yes — LLM respawn skill decides recovery plan |
| `tool_low_durability` | Main hand tool durability ≤ 10% (checked every 5s, 60s cooldown) | Yes — LLM decides: equip backup, craft new, or idle |

**Key rule:** `activity_done` is NOT routed to LLM — it signals `PlanExecutor` and marks task done. Only `activity_stuck` triggers LLM intervention.

**`task_started` / `task_stopped`** — sent by `commands.js` for all activity start/stop commands (including direct `!` commands). Python stores the task in `task_memory` so the player can say "繼續" to resume even after manual `!mine` / `!stopmine`.

### Bridge State Sent to Python

Every `sendState` call includes:
- `mode` — current operating mode (`companion` / `survival` / `workflow`)
- `activity` — top frame name or `'idle'` (backward compat)
- `stack` — full frame array without `resumeFn` (for LLM context)
- `pos`, `health`, `food`, `inventory`, `entities`, `chests`

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

`fish`, `stopfish`, `chop`, `stopchop`, `mine`, `stopmine`, `smelt`, `stopsmelt`, `combat`, `stopcombat`, `hunt`, `stophunt`, `getfood`, `stopgetfood`, `surface`, `stopsurface`, `explore`, `stopexplore`, `sethome`, `home`, `back`, `equip`, `unequip`, `come`, `look`, `tp`, `bury`, `clear`, `inv`, `setchest`, `labelchest`, `readchest`, `deposit`, `withdraw`, `makechest`, `setmode`, `resumetask`

### Other Key Modules

| Module | Purpose |
|--------|---------|
| `bridge.js` | WebSocket server, `sendState(bot, type, extra)` — includes `mode` field |
| `activity.js` | LIFO activity stack manager |
| `mode.js` | Operating mode: `getMode()`, `setMode(m)` — persisted to `agent/data/mode.json`. Values: `companion`, `survival`, `workflow` |
| `water.js` | Background water escape monitor (3-phase: swim up, pathfind dry, horizontal push) |
| `eating.js` | Auto-eats when food < 18, triggered by `bot.on('health')` |
| `inventory.js` | Pauses top activity when full, asks LLM via `inventory_full` |
| `crafting.js` | `ensureToolFor`, `ensurePickaxeTier`, `applyCraftDecision` |
| `equipment.js` | `equipBestLoadout`, `equipSpecific`, `unequipAll` |
| `world.js` | Utility: `findNearestPlayer`, entity/block helpers |
| `buried.js` | Tracks positions where items were buried (avoids re-digging) |
| `home.js` | Base location: `setHome`, `goHome`, `getHome`, `back` — persisted to `agent/data/home.json`. `back` tps to top activity's `startPos` and calls `resumeCurrent`. |
| `chest.js` | Chest storage: `setChest`, `labelChest`, `readChest`, `depositToChest`, `withdrawFromChest`, `getChests` — persisted to `agent/data/chests.json`. Labels: `food`, `wood`, `stone`, `ore`, `misc`. |

---

## Python Agent (`agent/`)

### Event Routing (`agent/agent.py`)

```python
HANDLERS = {
    "inventory_full":   inventory_skill.handle,
    "craft_decision":   craft_decision_skill.handle,
    "activity_stuck":   activity_stuck_skill.handle,
    "food_low":         food_skill.handle,
    "tick":             self_task_skill.handle,
    "action_done":      _on_done,          # signals PlanExecutor
    "activity_done":    _on_done,          # signals PlanExecutor
    "task_started":     _on_task_started,
    "task_stopped":     _on_task_stopped,
    "player_died":      _on_player_died,   # aborts executor, writes death.json
    "player_respawned": _on_player_respawned,  # LLM respawn skill
    "chat":             planner_skill.handle,
}
```

Handlers receive `state: dict` (full bot state) and `llm: LLMClient`. Return a list of command dicts, a plan dict, or `None`.

**Plan response** (`{"action": "plan", "commands": [...], "goal": "..."}`) is routed to `PlanExecutor`, which sequences commands waiting for `action_done`/`activity_done` between steps. Each step's status is tracked in `task_memory`. On completion, bot chats a summary (e.g. "完成！equip✓ getfood✓ mine✓").

**Replan response** (`{"action": "replan", "commands": [...]}`) — only valid from `activity_stuck` skill during executor run. Replaces remaining steps in executor without stopping the plan. Sent to `executor.replan()`.

**task_memory** (`agent/task_memory.py`) — persists current task to `agent/data/task.json`. Schema:
```json
{
  "id": "abc12345",
  "goal": "幫我挖鑽石",
  "commands": ["equip", "getfood", "mine diamond 10"],
  "steps": [
    {"cmd": "equip",          "status": "done",    "error": null},
    {"cmd": "getfood",        "status": "done",    "error": null},
    {"cmd": "mine diamond 10","status": "running", "error": null}
  ],
  "currentStep": 2,
  "status": "running",
  "interruptedBy": null
}
```
`status` per step: `pending` / `running` / `done` / `failed`. On resume, skips already-done steps. Populated by both executor (via planner) and `task_started` events (direct `!` commands).

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

Canonical definitions live in `agent/skills/commands_ref.py`. Use `command_list(keys)` to generate formatted prompt sections — do not duplicate command descriptions inline in skill files.

`fish`, `chop`, `mine` (args: ore type count), `smelt` (args: material count), `combat`, `hunt`, `getfood`, `surface`, `explore` (args: target), `equip`, `come` (args: [player]), `home`, `back`, `tp` (args: x y z), `setmode` (args: mode), `deposit` (args: chest_id), `withdraw` (args: item [count] chest_id), `chat`, `idle`

### Existing Skills

| File | Triggered by | Purpose |
|------|-------------|---------|
| `inventory.py` | `inventory_full` | Drop/plan — LLM decides drop, deposit+resume plan, or continue |
| `craft_decision.py` | `craft_decision` | Decide what to craft |
| `activity_stuck.py` | `activity_stuck` | 單步恢復；executor 執行中時附帶 plan_context，LLM 可回傳 `{"action":"replan","commands":[...]}` 替換剩餘步驟 |
| `food.py` | `food_low` | 補充食物（確定性邏輯，不呼叫 LLM） |
| `planner.py` | `chat` event | 自然語言 → command 序列；偵測「繼續」→ 從 task_memory 恢復（跳過已 done 步驟） |
| `respawn.py` | `player_respawned` | LLM 決定重生後恢復計畫（tp 回原位 / 直接繼續 / equip / idle） |
| `tool_durability.py` | `tool_low_durability` | LLM 決定工具耐久不足時的處理（換備用工具 / 合成新工具 / idle） |
| `self_task.py` | `tick` (idle, 60s) | 自主任務規劃；companion mode 不執行；workflow mode 自動恢復中斷任務 |
| `task_arbitration.py` | called by `_handle_player_chat` | 判斷玩家訊息是否 interrupt/queue/defer 當前任務 |

### Shared Skill Utilities (`agent/skills/`)

| File | Purpose |
|------|---------|
| `commands_ref.py` | Canonical command registry — `COMMANDS` dict + `command_list(keys)` helper. Single source of truth for all LLM-issuable commands. Import and call `command_list(["mine","chop",...])` to generate prompt sections. |
| `state_summary.py` | `summary_json(state)` — full state summary for LLM context. `equipment_summary(state)` — formatted equipment string (main hand + armor slots). |

### Operating Modes (`agent/data/mode.json`)

| Mode | `self_task` 行為 | 說明 |
|------|-----------------|------|
| `companion` | 不執行 | 純跟隨/支援玩家，不自主規劃 |
| `survival` | 執行（補食/補工具） | 預設模式，自主維持基本生存 |
| `workflow` | 執行 + 自動恢復中斷任務 | 任務導向，idle 時自動繼續未完成計畫 |

切換：`!setmode companion` / `!setmode survival` / `!setmode workflow`（或 LLM 送 `setmode` 指令）

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
6. **Command descriptions live in `commands_ref.py`** — never duplicate command usage/examples inline in skill prompts. Import `command_list(keys)` instead.
7. **Death → LLM** — on `player_died`, executor is aborted and task interrupted. On `player_respawned`, `respawn.py` skill decides the recovery plan (tp back, equip, continue, or idle).
