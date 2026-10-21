const activityStack = require('./activity')
const bridge = require('./bridge')

// Evidence is independent of touch(): retrying the same target must not extend
// a deadline. Position novelty handles travel; counters handle actual output.
class ProgressWatch {
    constructor(now = Date.now) { this.now = now; this.reset() }
    reset() { this.key = null; this.lastReport = -Infinity }
    inspect(bot, frame, runtime, options = {}) {
        const now = this.now()
        const key = `${frame.activity}:${frame.startTime}`
        if (key !== this.key) {
            this.key = key
            this.cells = new Set()
            this.high = {}
            this.lastEvidence = now
            this.lastOutcome = now
            this.lastReport = -Infinity
            this.samples = []
        }
        const pos = bot.entity?.position
        const cell = pos && [pos.x, pos.y, pos.z].map(v => Math.floor(v / 2)).join(',')
        const counts = {}
        for (const item of bot.inventory?.items?.() || []) counts[`item:${item.name}`] = (counts[`item:${item.name}`] || 0) + item.count
        for (const [name, value] of Object.entries(frame.progress || {})) {
            if (typeof value === 'number' && Number.isFinite(value)) counts[`progress:${name}`] = value
        }
        let effect = false
        let outcome = false
        for (const [name, value] of Object.entries(counts)) {
            const increased = this.samples.length && value > (this.high[name] || 0)
            if (increased) effect = true
            const target = frame.goal?.target
            const item = name.replace(/^item:/, '')
            const relevant = !target || [target, `raw_${target}`, `${target}_ore`, `deepslate_${target}_ore`,
                ...(target === 'stone' ? ['cobblestone'] : [])].includes(item)
            if (increased && (name.startsWith('progress:') || relevant)) outcome = true
            this.high[name] = Math.max(this.high[name] || 0, value)
        }
        const novel = cell && !this.cells.has(cell)
        if (novel) {
            this.cells.add(cell)
            if (this.cells.size > 512) this.cells.delete(this.cells.values().next().value)
        }
        if (novel || effect) this.lastEvidence = now
        if (outcome) this.lastOutcome = now
        this.samples.push({ at: now, cell, progress: { ...frame.progress } })
        if (this.samples.length > 8) this.samples.shift()
        const timeout = options.timeoutMs ?? (frame.activity === 'fishing' ? 60000 : 25000)
        const wait = runtime.wait
        const waiting = wait && now < wait.until
        let reason = null
        if (waiting) return null
        if (runtime.paused || !runtime.running) {
            if (now - (runtime.lastStateAt ?? now) >= (options.pauseTimeoutMs ?? 45000)) reason = 'orphaned_pause'
        } else if (wait && now >= wait.until) reason = 'wait_expired'
        else if (now - this.lastEvidence >= timeout) reason = 'no_progress'
        else if (['mining', 'chopping', 'hunting', 'getfood', 'smelting', 'fishing'].includes(frame.activity)
            && now - this.lastOutcome >= (options.reviewMs ?? 180000)) reason = 'no_task_progress'
        if (!reason || now - this.lastReport < 30000) return null
        this.lastReport = now
        return {
            reason, activity_name: frame.activity, watchdog: true,
            goal: frame.goal, progress: frame.progress,
            detail: `${frame.activity}: ${reason}; no new position/output for ${Math.round((now - this.lastEvidence) / 1000)}s`,
            progress_evidence: { no_effect_ms: now - this.lastEvidence, no_output_ms: now - this.lastOutcome, recent: this.samples.slice() },
        }
    }
}

let timer = null
const watch = new ProgressWatch()
function monitor(bot) {
    const frame = activityStack.getTopFrame()
    if (!frame || !bot.entity || bot.health <= 0) { watch.reset(); return }
    const event = watch.inspect(bot, frame, activityStack.getRuntimeState(frame.activity), activityStack.getActivityOptions(frame.activity))
    if (!event) return
    activityStack.pause(bot)
    // Halt physical motion immediately, before Python/CLI latency.
    require('./navigation').cancel(bot)
    bot.pathfinder?.setGoal(null)
    bot.stopDigging?.()
    bot.clearControlStates?.()
    bridge.sendState(bot, 'activity_stuck', event)
}
function startMonitor(bot) {
    if (!timer) timer = setInterval(() => monitor(bot), 2000)
}
module.exports = { startMonitor, ProgressWatch }
