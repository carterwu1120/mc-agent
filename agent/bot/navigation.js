// Bounded, terrain-aware navigation for the mining pilot. No Minecraft connection
// is created here; the existing Pathfinder plans dig/place actions from live blocks.
const { goals } = require('mineflayer-pathfinder')
const { createMovements } = require('./movement_prefs')

const states = new WeakMap()
const BUILD_BLOCKS = ['cobblestone', 'cobbled_deepslate', 'dirt', 'stone', 'andesite', 'diorite']

class FailureLedger {
    constructor({ ttl = 120000, limit = 128, now = Date.now } = {}) {
        this.entries = new Map()
        this.ttl = ttl
        this.limit = limit
        this.now = now
    }
    prune() {
        for (const [key, entry] of this.entries) {
            if (this.now() - entry.at >= this.ttl) this.entries.delete(key)
        }
    }
    has(key) { this.prune(); return this.entries.has(key) }
    fail(key, reason) {
        this.prune()
        this.entries.delete(key)
        this.entries.set(key, { reason, at: this.now() })
        while (this.entries.size > this.limit) this.entries.delete(this.entries.keys().next().value)
    }
}

function stateFor(bot) {
    if (!states.has(bot)) states.set(bot, { ledger: new FailureLedger(), active: null, last: null })
    return states.get(bot)
}

function enabled() { return process.env.MC_ADAPTIVE_NAVIGATION === 'true' }
function position(bot) { return bot.entity.position.floored() }
function targetOf(goal) {
    return { kind: goal.constructor.name, x: goal.x, y: goal.y, z: goal.z, rangeSq: goal.rangeSq }
}

function terrainKey(bot, goal) {
    // Both local and target terrain matter. Unknown blocks remain unknown.
    const origins = [position(bot)]
    if ([goal.x, goal.y, goal.z].every(Number.isFinite)) origins.push(position(bot).clone().set(goal.x, goal.y, goal.z))
    const cells = []
    for (const origin of origins) {
        for (let x = -2; x <= 2; x++) for (let y = -1; y <= 2; y++) for (let z = -2; z <= 2; z++) {
            const block = bot.blockAt(origin.offset(x, y, z))
            cells.push(block ? (block.stateId ?? block.name) : '?')
        }
    }
    const inventory = bot.inventory.items().map(i => `${i.name}:${i.count}`).sort()
    return JSON.stringify([bot.game?.dimension, position(bot), targetOf(goal), cells, inventory])
}

function profiles(bot) {
    return ['walk', 'dig', 'build'].map(name => {
        const movements = createMovements(bot, {
            canDig: name !== 'walk', scaffoldBlockNames: name === 'build' ? BUILD_BLOCKS : [],
        })
        movements.allow1by1towers = name === 'build'
        movements.allowParkour = false
        movements.allowSprinting = false
        movements.maxDropDown = 2
        movements.infiniteLiquidDropdownDistance = false
        movements.digCost = 3
        movements.placeCost = 6
        return { name, movements }
    })
}

function stopOwnedGoal(bot, token) {
    if (token.goal && bot.pathfinder.goal === token.goal) bot.pathfinder.setGoal(null)
}

function cancel(bot) {
    if (!bot) return
    const token = stateFor(bot).active
    if (!token) return
    token.cancelled = true
    token.interrupt?.()
    stopOwnedGoal(bot, token)
    // A stack push/pop may resume in the same JS turn. The cancelled operation
    // must yield ownership immediately; its finalizer is token-checked.
    stateFor(bot).active = null
}

function error(reason) { const e = new Error(reason); e.reason = reason; return e }

function hasPlacementAnchors(bot, step) {
    const placed = new Set()
    for (const place of step.toPlace || []) {
        if (place.useOne) continue
        const ref = position(bot).clone().set(place.x, place.y, place.z)
        if (bot.blockAt(ref)?.boundingBox !== 'block' && !placed.has(ref.toString())) return false
        placed.add(ref.offset(place.dx, place.dy, place.dz).toString())
    }
    return true
}

async function executeSegment(bot, goal, token, timeoutMs, shouldContinue) {
    token.goal = goal
    let timer, poll
    try {
        const interrupted = new Promise((_, reject) => {
            token.interrupt = () => reject(error('cancelled'))
            timer = setTimeout(() => reject(error('segment_timeout')), timeoutMs)
            poll = setInterval(() => {
                if (token.cancelled || !shouldContinue()) reject(error('cancelled'))
            }, 50)
        })
        await Promise.race([bot.pathfinder.goto(goal), interrupted])
        if (token.cancelled || !shouldContinue()) throw error('cancelled')
        // Pathfinder 2.4.x can resolve goto on an empty path even without arrival.
        if (!goal.isEnd(position(bot))) throw error('arrival_not_verified')
    } catch (e) {
        // Another activity owns movement now: yield rather than changing its goal.
        if (token.cancelled || !shouldContinue() || ['GoalChanged', 'PathStopped'].includes(e.name)) {
            token.cancelled = true
            throw error('cancelled')
        }
        throw e
    } finally {
        clearTimeout(timer)
        clearInterval(poll)
        token.interrupt = null
        stopOwnedGoal(bot, token)
    }
}

async function navigate(bot, goal, {
    timeoutMs = 12000, segmentMs = 4000, maxSegments = 64,
    shouldContinue = () => true, onProgress = () => {},
} = {}) {
    const state = stateFor(bot)
    if (state.active) return { status: 'cancelled', reason: 'navigation_busy', target: targetOf(goal), attempts: [] }
    const token = { cancelled: false, goal: null }
    state.active = token
    state.latest = token
    const deadline = Date.now() + timeoutMs
    const attempts = []
    const originalMovements = bot.pathfinder.movements
    let segments = 0
    const finish = (status, reason) => {
        const result = { status, reason, target: targetOf(goal), attempts: attempts.slice(-12), segments }
        if (state.latest === token) state.last = result
        return result
    }
    try {
        while (segments < maxSegments && Date.now() < deadline) {
            if (token.cancelled || !shouldContinue()) return finish('cancelled', 'cancelled')
            if (goal.isEnd(position(bot))) return finish('success', 'arrived')
            const observation = terrainKey(bot, goal)
            let moved = false
            for (const profile of profiles(bot)) {
                if (token.cancelled || !shouldContinue()) return finish('cancelled', 'cancelled')
                const key = `${observation}:${profile.name}`
                if (state.ledger.has(key)) {
                    attempts.push({ profile: profile.name, reason: 'recent_failure' })
                    continue
                }
                const remaining = deadline - Date.now()
                if (remaining <= 0) break
                // Use the generator to respect a bounded planning slice; do not
                // discard partial searches after Pathfinder's default 40 ms tick.
                let result
                try {
                    const generator = bot.pathfinder.getPathFromTo(profile.movements, bot.entity.position, goal, {
                        timeout: Math.min(250, remaining), tickTimeout: Math.min(250, remaining),
                        optimizePath: false, searchRadius: 32,
                    })
                    try { result = generator.next().value.result } finally { generator.return() }
                } catch (e) {
                    state.ledger.fail(key, 'planner_error')
                    attempts.push({ profile: profile.name, reason: 'planner_error' })
                    continue
                }
                const path = result.path || []
                if (!path.length) {
                    state.ledger.fail(key, result.status)
                    attempts.push({ profile: profile.name, reason: result.status })
                    continue
                }
                // Execute only the next planned stand position, then observe again.
                const step = path[0]
                if (!hasPlacementAnchors(bot, step)) {
                    state.ledger.fail(key, 'no_placement_anchor')
                    attempts.push({ profile: profile.name, reason: 'no_placement_anchor' })
                    continue
                }
                const segmentGoal = new goals.GoalBlock(step.x, step.y, step.z)
                bot.pathfinder.setMovements(profile.movements)
                segments++
                try {
                    await executeSegment(bot, segmentGoal, token, Math.min(segmentMs, Math.max(1, deadline - Date.now())), shouldContinue)
                    onProgress({ profile: profile.name, at: position(bot), remaining: goal.heuristic(position(bot)) })
                    moved = true
                    break
                } catch (e) {
                    if (e.reason === 'cancelled') return finish('cancelled', 'cancelled')
                    state.ledger.fail(key, e.reason || e.name)
                    attempts.push({ profile: profile.name, reason: e.reason || e.name })
                }
            }
            if (!moved) return finish('blocked', Date.now() >= deadline ? 'navigation_timeout' : 'no_viable_route')
        }
        if (goal.isEnd(position(bot))) return finish('success', 'arrived')
        return finish('blocked', Date.now() >= deadline ? 'navigation_timeout' : 'segment_budget_exhausted')
    } finally {
        stopOwnedGoal(bot, token)
        // Do not overwrite movement preferences installed by an interrupting activity.
        if (!token.cancelled && shouldContinue() && originalMovements) bot.pathfinder.setMovements(originalMovements)
        if (state.active === token) state.active = null
    }
}

function lastResult(bot) { return stateFor(bot).last }
module.exports = { enabled, navigate, cancel, lastResult, profiles, FailureLedger }
