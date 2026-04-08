# JS Bot Development Guide

For architecture overview and event types, see agent/CLAUDE.md

---

## Activity Module Pattern

Every activity follows this exact pattern:

```js
const activityStack = require('./activity')
const bridge = require('./bridge')

let isActive = false
let _isPaused = false
let _progressVar = 0  // promoted from loop-local

activityStack.register('name', _pause)

function _pause(_bot) { isActive = false; _isPaused = true }

async function startX(bot, goal = {}) {
    if (isActive) return
    // NOTE: do NOT check checkFull here — commands.js already guards all activity starts
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
        // on goal reached: isActive = false; bridge.sendState(bot, 'activity_done', {}); break
        // on stuck:        isActive = false; bridge.sendState(bot, 'activity_stuck', { reason }); break
        // update progress: activityStack.updateProgress({ count: _progressVar })
    }
    if (!_isPaused) activityStack.pop(bot)  // pop only if not paused by another activity
    _isPaused = false
}
```

**Key rules:**
- `stopX` sets flags only — `_loop` calls `activityStack.pop(bot)` when it exits
- `_pause` sets `_isPaused = true` so `_loop` skips the pop (stack already handled by `push`)
- `startX` does NOT call `checkFull` — that's handled centrally in `commands.js`

---

## Activity Stack API (`activity.js`)

- `register(name, pauseFn)` — called once at module load
- `push(bot, name, goal, resumeFn)` — auto-pauses current top, pushes new frame
- `pop(bot)` — pops top frame, calls previous frame's `resumeFn`
- `pause(bot)` — calls registered `pauseFn` for top (no pop; used by inventory)
- `resumeCurrent(bot)` — calls top frame's `resumeFn` without popping (used by inventory)
- `updateProgress(data)` — merges data into top frame's `progress`
- `updateTopGoal(goal)` — updates top frame's goal, resets progress (used by `_resumeX`)
- `getActivity()` — top frame name or `'idle'`
- `getStack()` — all frames without `resumeFn` (JSON-safe, sent to Python)

Each frame stores:
```js
{
    activity:  'mining',
    goal:      { target: 'iron', count: 10 },  // immutable original goal
    progress:  { count: 5 },                   // updated during execution
    startPos:  { x: 100, y: -16, z: -200 },
    startTime: 1711411200000,
    resumeFn:  Function,                        // NOT sent to Python
}
```

---

## Existing Activity Modules

| Module | Activity name | Progress field | Stuck reasons |
|--------|--------------|---------------|---------------|
| `fishing.js` | `fishing` | `catches` | `bad_cast` |
| `woodcutting.js` | `chopping` | `logs` | — |
| `mining.js` | `mining` | `count` | `no_blocks` |
| `smelting.js` | `smelting` | `smelted` | `no_input`, `missing_dependency` |
| `combat.js` | `combat` | — | — |

---

## Inventory Interruption (Transient)

Inventory does **not** push its own frame — it's a transient interruption:

```js
activityStack.pause(bot)       // pause current top
// ... handle inventory ...
activityStack.resumeCurrent(bot)  // resume current top
```

`inventory.js` exports `checkFull(bot)` — returns `true` and triggers `_handleFull` if slots ≥ 34.

---

## Command Dispatch (`commands.js`)

All commands from Python arrive in `handle(bot, msg)`. Before any activity starts, `commands.js` calls `checkFull(bot)` via `ACTIVITY_COMMANDS` set. Adding a new activity:

1. Add `case 'newactivity':` to the switch
2. Add `'newactivity'` to `ACTIVITY_COMMANDS` set at top of file
3. Send `task_started` / `task_stopped` events as other cases do

---

## Bridge State Sent to Python (`bridge.js`)

Every `sendState` call includes:
```js
{
    mode,        // 'companion' | 'survival' | 'workflow'
    activity,    // top frame name or 'idle'
    stack,       // all frames without resumeFn
    pos, health, food,
    inventory,   // [{name, count, durability_pct?}]
    inventory_slots: { used, total: 36, free },
    equipment,   // {main_hand, off_hand, armor: {head, torso, legs, feet}} — items as {name, durability_pct}
    chests,      // from getChests() — [{id, pos, label, contents, freeSlots, ...}]
    entities,    // nearby entities (up to 20)
}
```

Durability: items with `maxDurability` include `durability_pct` (0–100). Broken items show 0%.
