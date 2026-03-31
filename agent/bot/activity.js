const _stack = []             // stack frames, last element = top (current activity)
const _registry = new Map()  // name → pauseFn(bot)
const _runtime = new Map()   // name → runtime metadata

function _stackLabel() {
    return _stack.map(frame => frame.activity).join(' > ') || 'idle'
}

function _now() {
    return Date.now()
}

function _ensureRuntime(name) {
    if (!_runtime.has(name)) {
        _runtime.set(name, {
            running: false,
            paused: false,
            startedAt: null,
            lastProgressAt: null,
            lastProgressReason: null,
            lastStateAt: null,
            lastStateReason: null,
        })
    }
    return _runtime.get(name)
}

function markStarted(name, reason = 'started') {
    const rt = _ensureRuntime(name)
    const now = _now()
    rt.running = true
    rt.paused = false
    rt.startedAt ??= now
    rt.lastProgressAt = now
    rt.lastProgressReason = reason
    rt.lastStateAt = now
    rt.lastStateReason = reason
}

function markPaused(name, reason = 'paused') {
    const rt = _ensureRuntime(name)
    rt.running = false
    rt.paused = true
    rt.lastStateAt = _now()
    rt.lastStateReason = reason
}

function markStopped(name, reason = 'stopped') {
    const rt = _ensureRuntime(name)
    rt.running = false
    rt.paused = false
    rt.startedAt = null
    rt.lastProgressAt = null
    rt.lastProgressReason = null
    rt.lastStateAt = _now()
    rt.lastStateReason = reason
}

function touch(name, reason = 'progress') {
    const rt = _ensureRuntime(name)
    const now = _now()
    rt.lastProgressAt = now
    rt.lastProgressReason = reason
    rt.lastStateAt = now
    rt.lastStateReason = reason
}

function getRuntimeState(name) {
    return { ..._ensureRuntime(name) }
}

function isTopActivity(name) {
    return getActivity() === name
}

function isStale(name, maxIdleMs = 15000) {
    const rt = _ensureRuntime(name)
    if (!rt.running) return false
    if (!isTopActivity(name)) return true
    if (!rt.lastProgressAt) return true
    return (_now() - rt.lastProgressAt) > maxIdleMs
}

function forget(name) {
    const before = _stack.length
    for (let i = _stack.length - 1; i >= 0; i--) {
        if (_stack[i].activity === name) {
            _stack.splice(i, 1)
        }
    }
    if (_stack.length !== before) {
        console.log(`[Activity] forget ${name} -> ${_stackLabel()}`)
    }
    markStopped(name, 'forgotten')
}

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
    markStarted(name, 'push')
    console.log(`[Activity] push ${name} -> ${_stackLabel()}`)
}

// Pop the top frame and resume the previous activity (if any).
function pop(bot) {
    if (_stack.length === 0) return
    const popped = _stack.pop()
    markStopped(popped.activity, 'pop')
    console.log(`[Activity] pop ${popped.activity} -> ${_stackLabel()}`)
    if (_stack.length > 0) {
        const prev = _stack[_stack.length - 1]
        console.log(`[Activity] resume ${prev.activity}`)
        if (prev.resumeFn) prev.resumeFn(bot)
    }
}

// Pause the current top activity's loop without touching the stack.
// Used internally by push() and by inventory.js (transient interruption).
function pause(bot) {
    const top = _stack[_stack.length - 1]
    if (!top) return
    markPaused(top.activity, 'pause')
    console.log(`[Activity] pause ${top.activity}`)
    const pauseFn = _registry.get(top.activity)
    if (pauseFn) pauseFn(bot)
}

// Resume the current top frame's activity without popping.
// Used by inventory.js after it finishes a transient interruption.
function resumeCurrent(bot) {
    const top = _stack[_stack.length - 1]
    if (top) {
        console.log(`[Activity] resumeCurrent ${top.activity}`)
        if (top.resumeFn) top.resumeFn(bot)
    }
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
    markStarted, markPaused, markStopped, touch,
    getRuntimeState, isTopActivity, isStale, forget,
}
