const activityStack = require('./activity')
const bridge = require('./bridge')

const CHECK_INTERVAL_MS = 3000
const DEFAULT_TIMEOUT_MS = 20000
const MIN_PROGRESS_MOVE_DIST = 1.25
const LEGACY_ACTIVITY_TIMEOUT_MS = {
    hunting: 25000,
    mining: 20000,
    smelting: 25000,
    combat: 15000,
    chopping: 20000,
    fishing: 20000,
    getfood: 15000,
    surface: 15000,
    explore: 25000,
}

const LEGACY_SUGGESTED_ACTIONS = {
    hunting: ['surface', 'back', 'home', 'idle'],
    mining: ['back', 'surface', 'home', 'idle'],
    smelting: ['back', 'surface', 'home', 'chat'],
    combat: ['back', 'home', 'chat', 'idle'],
    chopping: ['surface', 'back', 'explore', 'idle'],
    fishing: ['back', 'explore', 'chat', 'idle'],
    getfood: ['hunt', 'fish', 'chat', 'idle'],
    surface: ['back', 'home', 'chat', 'idle'],
    explore: ['back', 'home', 'chat', 'idle'],
}

let _timer = null
let _lastReportedKey = null
let _lastObservation = null

function _timeoutFor(activity) {
    const options = activityStack.getActivityOptions(activity)
    return options.timeoutMs ?? LEGACY_ACTIVITY_TIMEOUT_MS[activity] ?? DEFAULT_TIMEOUT_MS
}

function _suggestedActionsFor(activity) {
    const options = activityStack.getActivityOptions(activity)
    return options.suggestedActions ?? LEGACY_SUGGESTED_ACTIONS[activity] ?? ['back', 'chat', 'idle']
}

function _snapshot(bot, activity, frame) {
    const pos = bot.entity?.position
        ? { x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z }
        : null
    const items = (bot.inventory?.items?.() || [])
        .map(i => `${i.name}:${i.count}`)
        .sort()
        .join('|')
    const held = bot.heldItem?.name || null
    const goal = JSON.stringify(frame?.goal || {})
    const progress = JSON.stringify(frame?.progress || {})
    return {
        key: `${activity}:${frame?.startTime ?? 0}`,
        pos,
        items,
        held,
        goal,
        progress,
    }
}

function _distance(a, b) {
    if (!a || !b) return 0
    const dx = a.x - b.x
    const dy = a.y - b.y
    const dz = a.z - b.z
    return Math.sqrt(dx * dx + dy * dy + dz * dz)
}

function _detectPhysicalProgress(bot, activity, frame) {
    const snap = _snapshot(bot, activity, frame)
    if (!_lastObservation || _lastObservation.key !== snap.key) {
        _lastObservation = snap
        return
    }

    if (_distance(snap.pos, _lastObservation.pos) >= MIN_PROGRESS_MOVE_DIST) {
        activityStack.touch(activity, 'position_changed')
    } else if (snap.items !== _lastObservation.items) {
        activityStack.touch(activity, 'inventory_changed')
    } else if (snap.held !== _lastObservation.held) {
        activityStack.touch(activity, 'held_item_changed')
    } else if (snap.goal !== _lastObservation.goal) {
        activityStack.touch(activity, 'goal_changed')
    } else if (snap.progress !== _lastObservation.progress) {
        activityStack.touch(activity, 'progress_changed')
    }

    _lastObservation = _snapshot(bot, activity, frame)
}

function _reportKey(activity, runtime) {
    return `${activity}:${runtime.lastProgressAt ?? 0}:${runtime.lastProgressReason ?? 'none'}`
}

function _detailFor(activity, runtime, timeoutMs) {
    const idleMs = Date.now() - (runtime.lastProgressAt ?? runtime.startedAt ?? Date.now())
    const seconds = Math.max(1, Math.round(idleMs / 1000))
    const lastReason = runtime.lastProgressReason || 'unknown'
    return `${activity} 已超過 ${Math.round(timeoutMs / 1000)} 秒沒有進展（最近進展: ${lastReason}，約 ${seconds} 秒前）`
}

function _monitor(bot) {
    const activity = activityStack.getActivity()
    if (!activity || activity === 'idle') {
        _lastReportedKey = null
        _lastObservation = null
        return
    }

    const runtime = activityStack.getRuntimeState(activity)
    if (!runtime.running || runtime.paused) return

    const stack = activityStack.getStack()
    const top = stack[stack.length - 1] || {}
    _detectPhysicalProgress(bot, activity, top)

    const timeoutMs = _timeoutFor(activity)
    if (!activityStack.isStale(activity, timeoutMs)) {
        _lastReportedKey = null
        return
    }

    const key = _reportKey(activity, runtime)
    if (_lastReportedKey === key) return
    _lastReportedKey = key

    const detail = _detailFor(activity, runtime, timeoutMs)
    console.log(`[Watchdog] 偵測 ${activity} 無進展，觸發 activity_stuck: ${detail}`)
    activityStack.pause(bot)
    bridge.sendState(bot, 'activity_stuck', {
        activity_name: activity,
        reason: 'no_progress',
        detail,
        suggested_actions: _suggestedActionsFor(activity),
        watchdog: true,
        goal: top.goal || {},
        progress: top.progress || {},
    })
}

function startMonitor(bot) {
    if (_timer) return
    _timer = setInterval(() => _monitor(bot), CHECK_INTERVAL_MS)
    console.log('[Watchdog] 進度監控已啟動')
}

module.exports = { startMonitor }
