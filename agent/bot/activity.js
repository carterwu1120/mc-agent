const _stack = []             // stack frames, last element = top (current activity)
const _registry = new Map()  // name → pauseFn(bot)

// ── Registration ─────────────────────────────────────────────────────────────

function register(name, pauseFn) {
    _registry.set(name, pauseFn)
}

// ── Stack operations ──────────────────────────────────────────────────────────

// Push a new activity onto the stack. Auto-pauses the current top.
function push(bot, name, goal, resumeFn) {
    if (_stack.length > 0) {
        pause(bot)
    }
    const startPos = bot.entity?.position
        ? { x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z }
        : null
    _stack.push({ activity: name, goal: { ...goal }, progress: {}, startPos, startTime: Date.now(), resumeFn })
}

// Pop the top frame and resume the previous activity (if any).
function pop(bot) {
    if (_stack.length === 0) return
    _stack.pop()
    if (_stack.length > 0) {
        const prev = _stack[_stack.length - 1]
        if (prev.resumeFn) prev.resumeFn(bot)
    }
}

// Pause the current top activity's loop without touching the stack.
// Used internally by push() and by inventory.js (transient interruption).
function pause(bot) {
    const top = _stack[_stack.length - 1]
    if (!top) return
    const pauseFn = _registry.get(top.activity)
    if (pauseFn) pauseFn(bot)
}

// Resume the current top frame's activity without popping.
// Used by inventory.js after it finishes a transient interruption.
function resumeCurrent(bot) {
    const top = _stack[_stack.length - 1]
    if (top && top.resumeFn) top.resumeFn(bot)
}

// ── Progress / goal updates ───────────────────────────────────────────────────

function updateProgress(data) {
    const top = _stack[_stack.length - 1]
    if (top) Object.assign(top.progress, data)
}

// Update the top frame's goal and reset its progress (used by _resumeX internals).
function updateTopGoal(goal) {
    const top = _stack[_stack.length - 1]
    if (top) { top.goal = { ...goal }; top.progress = {} }
}

// ── Accessors ─────────────────────────────────────────────────────────────────

function getActivity() {
    return _stack.length > 0 ? _stack[_stack.length - 1].activity : 'idle'
}

// Returns all frames with resumeFn stripped (JSON-safe for Python).
function getStack() {
    return _stack.map(({ resumeFn, ...rest }) => rest)
}

module.exports = {
    register, push, pop, pause, resumeCurrent,
    updateProgress, updateTopGoal,
    getActivity, getStack,
}
