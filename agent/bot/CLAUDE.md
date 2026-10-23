# JS Bot Development Guide

For architecture overview and event types, see agent/CLAUDE.md

---

## Activity Module Pattern

### Adaptive mining navigation pilot

`MC_ADAPTIVE_NAVIGATION=true` routes mining `_goto` calls through `navigation.js`.
Pathfinder generates local walk/dig/build candidates; execute only the next
stand position and re-observe. Walking/digging profiles cannot scaffold; build
uses non-gravity full blocks. `movement_prefs.js` must map scaffold names to
**item IDs**, because Pathfinder counts inventory items, not block states.
Each segment verifies actual arrival and has cancellation/time budgets. Failed
strategies are cached per position/goal/local terrain/inventory for 120 seconds,
up to 128 entries, across stack resume and repeated mine commands in one process.
Cancellation only clears the goal owned by that operation. Mining loop generations
are propagated through AsyncLocalStorage, so resumed flags cannot revive old helpers.
Adaptive resume retains the resource objective but discards the old resume-position route.
The pilot does not replace all manual tunnel/descent/escape routines or coordinate
every other activity's movement. Keep it opt-in until Minecraft integration tests pass.
Bridge state includes the last bounded `navigation` result, consumed by stuck prompts.
Offline tests use real Pathfinder planning and simulated action application:
`npm.cmd test --prefix agent/bot`. They do not validate live physics/server placement.

`index.js` loads project-root `.env` through `config.js` before environment-reading
modules. Explicit process variables win. Node.js 22+ is required. `MC_FORGE=false`
disables the previously unconditional Forge handshake for vanilla LAN testing.
`npm.cmd run check --prefix agent/bot` prints connection settings without connecting.

Routine monitors must not flood logs or retry impossible crafting on every loop.
`eating.js` reports an empty food inventory at most once per minute;
`combat.js` attempts sword crafting at most once per 30 seconds and emits an armor
material decision at most once per minute; `crafting.js` rate-limits the missing
workbench-material message. These cooldowns suppress identical attempts only;
inventory, capability and environment state remains present in bridge snapshots.

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
- `touch(name, reason)` — records diagnostics; watchdog independently verifies evidence
- `waitFor(reason, timeoutMs)` / `endWait()` — bounded expected waits
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

## Progress Semantics and Watchdog

`ProgressWatch` in watchdog.js uses its own evidence timeline, independently of
`touch()`. New 2-block spatial cells and inventory/counter high-water marks count
as local effects. Repeated targets, gear swaps, equal counters and short oscillations
cannot refresh the deadline. The visited-cell history is bounded at 512 entries.
For targeted mining, unrelated byproducts do not count as requested resource output.

Check interval: 2s. No-effect timeout: 25s, fishing 60s; activity metadata can override
`timeoutMs`. Resource activities moving without output trigger review after 180s
(`reviewMs`). Paused/nonrunning top frames trigger after 45s (`pauseTimeoutMs`).
Idle-with-unfinished-task is handled by Python, where task ownership is known.

`waitFor(reason, timeoutMs)` gives the top activity a bounded expected wait (max 120s).
Repeated calls with the same reason cannot extend it. `endWait()` clears it after
observable completion. Inventory uses 120s, furnace output 30s, fish bite 35s.
Do not use touch('waiting') to manufacture progress. `updateProgress` ignores equal
values, and catches increment only when inventory actually gained an item.

Watchdog pauses the top frame and clears navigation/digging/controls before sending
activity_stuck with reason and a bounded `progress_evidence` history. Duplicate
reports are limited to one per 30s. Python then requests a full suspend handshake.

## Cancellation and command ownership

`execution.js` uses AsyncLocalStorage scopes plus a global execution epoch.
Command dispatch creates a scope with `_command_id`; exported activity starts are
wrapped with `execution.owned()` so nested activities get independent scopes.
Each frame retains its scope and original command ID (scope itself is not serialized).
Pause/pop/remove revoke the old scope. Resume creates a fresh scope, preserving the
original command ID. Activity loops must return after stale async continuations.

`execution.install(bot)` guards movement, dig/place, equipment, item/window operations
and returned chest/furnace methods. Stale async operations reject with AbortError;
stale synchronous mutations and bridge events are ignored. Guards do not undo packets
already sent to the server. New primitive APIs must be added to the guarded methods.
Background food/water/combat safety monitors remain available outside old task scopes.

`suspend` invalidates the global epoch, pauses/removes every stack frame without
resuming its parent, cancels inventory recovery and navigation, clears physical controls,
then emits `recovery_paused {request_id, stopped, errors}`. This is a local execution
acknowledgement, not proof that a previously sent server packet was rolled back.
It must never emit action_done. `stopall` also invalidates old scopes and clears motion.
Stopping a named activity present in the stack also clears its paused parent frames;
a manually stopped task must not silently revive after a later activity completes.
Bridge snapshots carry command_id and activity_runtime, including explicit wait leases.

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
