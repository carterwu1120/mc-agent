const execution = require('./execution')
const activityStack = require('./activity')
const bridge = require('./bridge')
const { sendActivityDone } = bridge
const { noteTeleportLikeAction } = require('./crafting')
const { applyMovements } = require('./movement_prefs')
const {
    isPassableSpace,
    isSafeGround,
    isSafeStandable,
    skyLightAt,
    teleportCommand,
    waitForSafeLanding,
} = require('./safe_spot')

let isSurfacing = false
let _isPaused = false
let _runToken = 0

function _worldMinY(bot) {
    return bot.game?.minY ?? -64
}

function _worldMaxY(bot) {
    const minY = _worldMinY(bot)
    return minY + (bot.game?.height ?? 384) - 1
}

function _candidateScore(bot, pos) {
    const dist = bot.entity.position.distanceTo(pos.offset(0.5, 0, 0.5))
    const dyPenalty = Math.max(0, pos.y - bot.entity.position.y) * 0.35
    return dist + dyPenalty
}

function _setEscapeMovements(bot) {
    applyMovements(bot, {
        canDig: true,
        scaffoldBlockNames: ['cobbled_deepslate', 'cobblestone', 'dirt', 'stone', 'andesite', 'diorite', 'gravel', 'sand'],
    })
}

// Bottom y of the 16-block chunk section containing y, if that section is all air.
// Most of a column above the surface is empty sections; skipping them avoids hundreds
// of blockAt calls per column (a deep search used to block the bot for seconds).
function _emptySectionBottom(column, y) {
    if (!column?.sections || column.minY === undefined) return null
    const index = (y - column.minY) >> 4
    if (column.sections[index]?.isEmpty?.() !== true) return null
    return column.minY + (index << 4)
}

// Topmost sky-lit standing spot in one column, or null. Scanning stops at the first
// solid block: nothing below it can see the sky, so the rest of the column is wasted work.
function _columnSurface(bot, base, dx, dz, minY, maxY) {
    let column = null
    try { column = bot.world?.getColumnAt?.(base.offset(dx, 0, dz)) } catch (_) {}
    for (let y = maxY - 2; y >= minY; y--) {
        // Land on the section's lowest block (it can still be the feet position).
        const bottom = _emptySectionBottom(column, y)
        if (bottom !== null && bottom < y) { y = bottom + 1; continue }
        const feet = bot.blockAt(base.offset(dx, y - base.y, dz))
        if (!feet) return null
        if (!isPassableSpace(feet)) return null
        const head = bot.blockAt(base.offset(dx, y + 1 - base.y, dz))
        const ground = bot.blockAt(base.offset(dx, y - 1 - base.y, dz))
        if (!head || !ground || !isPassableSpace(head)) continue
        if (isPassableSpace(ground)) continue
        if (!isSafeGround(ground) || skyLightAt(bot, feet.position) < 14) return null
        return feet.position.clone()
    }
    return null
}

// Search rings outward from the bot (including the column straight above it). Within
// maxRadius every column is checked; beyond it, up to farRadius, every other ring and
// column, which keeps a deep underground search bounded when the nearby surface is
// water or tree canopy.
function findSurfaceSpot(bot, maxRadius = 24, farRadius = 64) {
    if (bot.game?.dimension && bot.game.dimension !== 'overworld') {
        return null
    }

    const base = bot.entity.position.floored()
    const minY = Math.max(base.y, _worldMinY(bot) + 1)
    const maxY = _worldMaxY(bot)

    for (let r = 0; r <= farRadius; r++) {
        const sparse = r > maxRadius
        if (sparse && r % 2) continue
        const candidates = []
        for (let dx = -r; dx <= r; dx++) {
            for (let dz = -r; dz <= r; dz++) {
                if (Math.max(Math.abs(dx), Math.abs(dz)) !== r) continue
                if (sparse && (dx + dz) % 2) continue
                const pos = _columnSurface(bot, base, dx, dz, minY, maxY)
                if (pos) candidates.push({ pos, score: _candidateScore(bot, pos) })
            }
        }
        if (candidates.length > 0) {
            candidates.sort((a, b) => a.score - b.score)
            return candidates[0].pos
        }
    }
    return null
}

activityStack.register('surface', _pause)

function _pause(_bot) {
    _runToken++
    isSurfacing = false
    _isPaused = true
    console.log('[Surface] 暫停前往地表')
}

async function startSurfacing(bot, goal = {}) {
    if (isSurfacing) {
        console.log('[Surface] 已在前往地表中')
        return
    }
    isSurfacing = true
    _isPaused = false
    _runToken += 1
    activityStack.push(bot, 'surface', goal, (b) => _resumeSurfacing(b, goal))
    console.log('[Surface] 開始前往地表')
    _run(bot, goal, _runToken)
}

function _resumeSurfacing(bot, goal) {
    if (isSurfacing) return
    isSurfacing = true
    _isPaused = false
    _runToken += 1
    console.log('[Surface] 恢復前往地表')
    _run(bot, goal, _runToken)
}

function stopSurfacing(bot) {
    if (!isSurfacing) return
    isSurfacing = false
    _isPaused = false
    _runToken += 1
    try {
        bot.pathfinder?.setGoal(null)
    } catch (_) {}
    try {
        bot.clearControlStates?.()
    } catch (_) {}
    console.log('[Surface] 停止前往地表')
}

async function _run(bot, goal = {}, token) {
    try {
        const radius = Number.isFinite(goal.radius) ? goal.radius : 24
        const target = findSurfaceSpot(bot, radius)
        if (!target || !isSurfacing || token !== _runToken) {
            bridge.sendState(bot, 'activity_stuck', {
                activity_name: 'surface',
                reason: 'no_surface_found',
                detail: '64 格內找不到可站立的露天地表（可能上方是水或樹冠）',
            })
            return
        }

        _setEscapeMovements(bot)
        bot.pathfinder?.setGoal(null)
        console.log(`[Surface] 嘗試傳送到地表 (${target.x}, ${target.y}, ${target.z})`)
        noteTeleportLikeAction()
        bot.chat(teleportCommand(bot.username, target))
        const landed = await waitForSafeLanding(bot, target, {
            requireSky: true,
            timeoutMs: 3500,
        })
        if (!execution.isCurrent()) return
        if (token !== _runToken || !isSurfacing) return

        if (landed && isSafeStandable(bot, landed, { requireSky: true })) {
            console.log('[Surface] 已抵達地表')
            sendActivityDone(bot, 'surface')
            return
        }

        bridge.sendState(bot, 'activity_stuck', {
            activity_name: 'surface',
            reason: 'timeout',
            detail: '已嘗試傳送到附近地表，但未成功到達有效露天地表位置',
        })
    } catch (e) {
        if (token !== _runToken || !isSurfacing) return
        console.log(`[Surface] 失敗: ${e.message}`)
        bridge.sendState(bot, 'activity_stuck', {
            activity_name: 'surface',
            reason: 'timeout',
            detail: e.message,
        })
    } finally {
        if (!execution.isCurrent() || token !== _runToken) return
        const paused = _isPaused
        isSurfacing = false
        _isPaused = false
        if (!paused) activityStack.pop(bot)
    }
}

module.exports = { startSurfacing, stopSurfacing, findSurfaceSpot }

// Each activity has its own cancellable async scope, including nested activities.
for (const [name, fn] of Object.entries(module.exports)) {
    if (name.startsWith('start') && name !== 'startMonitor') module.exports[name] = execution.owned(fn)
}
