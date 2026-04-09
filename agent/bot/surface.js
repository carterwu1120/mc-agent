const activityStack = require('./activity')
const bridge = require('./bridge')
const { noteTeleportLikeAction } = require('./crafting')
const { applyMovements } = require('./movement_prefs')

const NON_SOLID = new Set([
    'air', 'cave_air', 'void_air', 'water', 'lava',
    'grass', 'tall_grass', 'fern', 'large_fern',
    'snow', 'vine', 'weeping_vines', 'twisting_vines',
])

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

function _isPassable(name) {
    if (!name) return true
    return NON_SOLID.has(name) || name.includes('leaves')
}

function _isSolidGround(name) {
    if (!name) return false
    return !_isPassable(name)
}

function _skyLightAt(bot, pos) {
    try {
        return bot.world?.getSkyLight?.(pos) ?? 0
    } catch (_) {
        return 0
    }
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

function _isSurfaceLike(bot) {
    const feet = bot.entity.position.floored()
    const sky = _skyLightAt(bot, feet)
    const head = bot.blockAt(feet.offset(0, 1, 0))
    const above = bot.blockAt(feet.offset(0, 2, 0))
    return sky >= 14 && _isPassable(head?.name) && _isPassable(above?.name)
}

async function _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms))
}

function findSurfaceSpot(bot, maxRadius = 24) {
    if (bot.game?.dimension && bot.game.dimension !== 'overworld') {
        return null
    }

    const base = bot.entity.position.floored()
    const minY = _worldMinY(bot)
    const maxY = _worldMaxY(bot)
    const startY = Math.max(base.y, minY + 1)
    const candidates = []

    for (let r = 1; r <= maxRadius; r++) {
        for (let dx = -r; dx <= r; dx++) {
            for (let dz = -r; dz <= r; dz++) {
                if (Math.max(Math.abs(dx), Math.abs(dz)) !== r) continue

                for (let y = maxY - 2; y >= startY; y--) {
                    const feet = bot.blockAt(base.offset(dx, y - base.y, dz))
                    const head = bot.blockAt(base.offset(dx, y + 1 - base.y, dz))
                    const ground = bot.blockAt(base.offset(dx, y - 1 - base.y, dz))

                    if (!feet || !head || !ground) continue
                    if (!_isPassable(feet.name) || !_isPassable(head.name)) continue
                    if (!_isSolidGround(ground.name)) continue

                    const light = _skyLightAt(bot, feet.position)
                    if (light < 14) continue

                    candidates.push({
                        pos: feet.position.clone(),
                        score: _candidateScore(bot, feet.position),
                    })
                    break
                }
            }
        }
        if (candidates.length > 0) break
    }

    candidates.sort((a, b) => a.score - b.score)
    return candidates[0]?.pos ?? null
}

activityStack.register('surface', _pause)

function _pause(_bot) {
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
                reason: 'timeout',
                detail: '找不到可靠的可站立地表位置',
            })
            return
        }

        _setEscapeMovements(bot)
        bot.pathfinder?.setGoal(null)
        console.log(`[Surface] 傳送到地表 (${target.x}, ${target.y}, ${target.z})`)
        noteTeleportLikeAction()
        bot.chat(`/tp ${bot.username} ${target.x} ${target.y} ${target.z}`)
        await _sleep(500)
        if (token !== _runToken || !isSurfacing) return

        const pos = bot.entity.position
        const arrived = Math.abs(pos.x - target.x) <= 1
            && Math.abs(pos.y - target.y) <= 1
            && Math.abs(pos.z - target.z) <= 1

        if (arrived && _isSurfaceLike(bot)) {
            console.log('[Surface] 已抵達地表')
            bridge.sendState(bot, 'activity_done', { activity: 'surface', reason: 'goal_reached' })
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
        if (token !== _runToken) return
        const paused = _isPaused
        isSurfacing = false
        _isPaused = false
        if (!paused) activityStack.pop(bot)
    }
}

module.exports = { startSurfacing, stopSurfacing, findSurfaceSpot }
