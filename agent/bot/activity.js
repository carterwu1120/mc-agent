const _stack = []             // stack frames, last element = top (current activity)
const execution = require('./execution')
const _registry = new Map()  // name → { pauseFn(bot), options }
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
    if (!execution.isCurrent()) return
    const rt = _ensureRuntime(name)
    const now = _now()
    rt.running = true
    rt.paused = false
    rt.wait = null
    rt.startedAt ??= now
    rt.lastProgressAt = now
    rt.lastProgressReason = reason
    rt.lastStateAt = now
    rt.lastStateReason = reason
}

function markPaused(name, reason = 'paused') {
    if (!execution.isCurrent()) return
    const rt = _ensureRuntime(name)
    rt.running = false
    rt.paused = true
    rt.lastStateAt = _now()
    rt.lastStateReason = reason
}

function markStopped(name, reason = 'stopped') {
    if (!execution.isCurrent()) return
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
    if (!execution.isCurrent()) return
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
    if (!execution.isCurrent()) return
    const before = _stack.length
    for (let i = _stack.length - 1; i >= 0; i--) {
        if (_stack[i].activity === name) {
            execution.cancelScope(_stack[i].scope)
            _stack.splice(i, 1)
        }
    }
    if (_stack.length !== before) {
        console.log(`[Activity] forget ${name} -> ${_stackLabel()}`)
    }
    markStopped(name, 'forgotten')
}

function has(name) {
    return _stack.some(frame => frame.activity === name)
}

// ── Registration ─────────────────────────────────────────────────────────────

function register(name, pauseFn, options = {}) {
    _registry.set(name, { pauseFn, options: { ...options } })
}

function getActivityOptions(name) {
    return { ...(_registry.get(name)?.options || {}) }
}

// ── Stack operations ──────────────────────────────────────────────────────────

// Push a new activity onto the stack. Auto-pauses the current top.
// Start position of the most recent task (bottom frame, not transient combat). Recovery
// suspends and manual stops clear the stack, but `back` must still know where work was.
let _lastTaskStartPos = null

function lastTaskStartPos() {
    return _lastTaskStartPos
}

function push(bot, name, goal, resumeFn) {
    if (!execution.isCurrent()) return
    if (_stack.length > 0) {
        pause(bot)
    }
    const startPos = bot.entity?.position
        ? { x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z }
        : null
    if (_stack.length === 0 && name !== 'combat' && startPos) _lastTaskStartPos = startPos
    _stack.push({ activity: name, goal: { ...goal }, progress: {}, startPos, startTime: Date.now(), resumeFn, commandId: execution.commandId(), scope: execution.current() })
    markStarted(name, 'push')
    console.log(`[Activity] push ${name} -> ${_stackLabel()}`)
}

// Pop the top frame and resume the previous activity (if any).
function pop(bot) {
    if (!execution.isCurrent()) return
    if (_stack.length === 0) return
    const popped = _stack.pop()
    markStopped(popped.activity, 'pop')
    execution.cancelScope(popped.scope)
    console.log(`[Activity] pop ${popped.activity} -> ${_stackLabel()}`)
    if (_stack.length > 0) {
        const prev = _stack[_stack.length - 1]
        console.log(`[Activity] resume ${prev.activity}`)
        if (prev.resumeFn) execution.run(() => { prev.scope = execution.current(); markStarted(prev.activity, 'resume'); prev.resumeFn(bot) }, prev.commandId)
    }
}

// Pause the current top activity's loop without touching the stack.
// Used internally by push() and by inventory.js (transient interruption).
function pause(bot) {
    if (!execution.isCurrent()) return
    const top = _stack[_stack.length - 1]
    if (!top) return
    markPaused(top.activity, 'pause')
    console.log(`[Activity] pause ${top.activity}`)
    const entry = _registry.get(top.activity)
    try { if (entry?.pauseFn) entry.pauseFn(bot) }
    finally { execution.cancelScope(top.scope) }
}

// Resume the current top frame's activity without popping.
// Used by inventory.js after it finishes a transient interruption.
function resumeCurrent(bot) {
    if (!execution.isCurrent()) return
    const top = _stack[_stack.length - 1]
    if (top) {
        console.log(`[Activity] resumeCurrent ${top.activity}`)
        if (top.resumeFn) execution.run(() => { top.scope = execution.current(); markStarted(top.activity, 'resume'); top.resumeFn(bot) }, top.commandId)
    }
}

// ── Progress / goal updates ───────────────────────────────────────────────────

function updateProgress(data) {
    if (!execution.isCurrent()) return
    const top = _stack[_stack.length - 1]
    if (top) {
        if (Object.keys(data).every(key => top.progress[key] === data[key])) return
        Object.assign(top.progress, data)
        touch(top.activity, 'update_progress')
    }
}

// A repeated request for the same wait cannot silently extend its lease.
function waitFor(reason, timeoutMs) {
    if (!execution.isCurrent()) return
    const top = _stack[_stack.length - 1]
    if (!top) return
    const rt = _ensureRuntime(top.activity)
    if (rt.wait?.reason === reason) return
    rt.wait = { reason, until: _now() + Math.min(120000, Math.max(1000, timeoutMs)) }
}

function endWait() {
    if (!execution.isCurrent()) return
    const top = _stack[_stack.length - 1]
    if (top) _ensureRuntime(top.activity).wait = null
}

function remove(bot, name, options = {}) {
    if (!execution.isCurrent()) return
    const { resumePrevious = false } = options
    const index = _stack.map(frame => frame.activity).lastIndexOf(name)
    if (index === -1) return

    const wasTop = index === _stack.length - 1
    const [removed] = _stack.splice(index, 1)
    markStopped(removed.activity, 'remove')
    execution.cancelScope(removed.scope)
    console.log(`[Activity] remove ${removed.activity} -> ${_stackLabel()}`)

    if (resumePrevious && wasTop && _stack.length > 0) {
        const prev = _stack[_stack.length - 1]
        console.log(`[Activity] resume ${prev.activity}`)
        if (prev.resumeFn) execution.run(() => { prev.scope = execution.current(); markStarted(prev.activity, 'resume'); prev.resumeFn(bot) }, prev.commandId)
    }
}

// Update the top frame's goal and reset its progress (used by _resumeX internals).
function updateTopGoal(goal) {
    if (!execution.isCurrent()) return
    const top = _stack[_stack.length - 1]
    if (top) {
        top.goal = { ...goal }
        top.progress = {}
        touch(top.activity, 'update_goal')
    }
}

function updateTopFrame(patch) {
    if (!execution.isCurrent()) return
    const top = _stack[_stack.length - 1]
    if (!top || !patch || typeof patch !== 'object') return
    Object.assign(top, patch)
}

function getTopFrame() {
    const top = _stack[_stack.length - 1]
    if (!top) return null
    const { resumeFn, scope, ...rest } = top
    return { ...rest }
}

// ── Accessors ─────────────────────────────────────────────────────────────────

function getActivity() {
    return _stack.length > 0 ? _stack[_stack.length - 1].activity : 'idle'
}

// Returns all frames with resumeFn stripped (JSON-safe for Python).
function getStack() {
    return _stack.map(({ resumeFn, scope, ...rest }) => rest)
}

function clearStack() {
    if (!execution.isCurrent()) return
    if (_stack.length === 0) return
    while (_stack.length > 0) {
        const frame = _stack.pop()
        markStopped(frame.activity, 'clear_stack')
        execution.cancelScope(frame.scope)
    }
    console.log('[Activity] clearStack -> idle')
}

module.exports = {
    waitFor, endWait,
    register, push, pop, remove, pause, resumeCurrent,
    updateProgress, updateTopGoal, updateTopFrame, getTopFrame,
    getActivity, getStack, clearStack, lastTaskStartPos,
    markStarted, markPaused, markStopped, touch,
    has,
    getRuntimeState, isTopActivity, isStale, forget, getActivityOptions,
}
