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

activityStack.register('name', _pause, {
    // optional overrides; omit unless this activity truly needs them
    timeoutMs: 25000,
    suggestedActions: ['back', 'chat', 'idle'],
})

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
- New activities automatically participate in no-progress monitoring even if they do not provide custom options

---

## Activity Stack API (`activity.js`)

- `register(name, pauseFn, options?)` — called once at module load; `options` may include `timeoutMs` and `suggestedActions`
- `push(bot, name, goal, resumeFn)` — auto-pauses current top, pushes new frame
- `pop(bot)` — pops top frame, calls previous frame's `resumeFn`
- `pause(bot)` — calls registered `pauseFn` for top (no pop; used by inventory)
- `resumeCurrent(bot)` — calls top frame's `resumeFn` without popping (used by inventory)
- `updateProgress(data)` — merges data into top frame's `progress`
- `updateTopGoal(goal)` — updates top frame's goal, resets progress (used by `_resumeX`)
- `getActivity()` — top frame name or `'idle'`
- `getStack()` — all frames without `resumeFn` (JSON-safe, sent to Python)
- `touch(name, reason)` — marks meaningful activity progress
- `getActivityOptions(name)` — returns registered metadata for watchdog / recovery

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

## Progress Semantics

Stuck detection is based on **progress**, not merely "still not done".

Use a layered model:

1. **Semantic progress**
   - Activity-specific signals that directly advance the goal.
   - Examples:
   - `mining`: dug block, target ore found, count incremented
   - `smelting`: placed furnace, inserted fuel/input, took output
   - `hunting`: target acquired, kill count incremented, drops collected
2. **Physical progress fallback**
   - Applied globally by `watchdog.js` to every top activity.
   - Signals:
   - position changed materially
   - inventory changed
   - held item changed
   - goal changed
   - top-frame progress changed
3. **No-progress timeout**
   - If the top activity is still running but no meaningful progress occurs before timeout, watchdog emits `activity_stuck(reason='no_progress')`.

Guidelines:
- Prefer `activityStack.touch(name, reason)` when a step clearly advances the current activity.
- Use `activityStack.updateProgress({...})` when the activity has a durable counter or measurable milestone.
- Do **not** treat random state churn (minor jitter, hunger ticks, damage taken) as semantic progress.
- New activities do not need a full custom progress implementation on day one; physical fallback covers them by default.
- If a new activity gets false positives or false negatives, add semantic progress hooks before adding special watchdog logic.

---

## Watchdog

`watchdog.js` monitors the current top activity only.

Rules:
- `idle` is ignored.
- The watchdog checks `lastProgressAt`, not `activity_done`.
- Long-running activities are valid as long as progress keeps occurring.
- Default timeout applies to all activities automatically.
- Only activities with special pacing should override `timeoutMs` in `register(...)`.

When watchdog fires, it sends:
- `type='activity_stuck'`
- `reason='no_progress'`
- `detail`
- `goal`
- `progress`
- `suggested_actions`

This is a JS-side mechanical signal. Python may then decide whether to retry, replan, or interrupt strategically.

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
