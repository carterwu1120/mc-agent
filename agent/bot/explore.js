const activityStack = require('./activity')
const bridge = require('./bridge')
const { noteTeleportLikeAction } = require('./crafting')

const NON_SOLID = new Set([
    'air', 'cave_air', 'void_air', 'water', 'lava',
    'grass', 'tall_grass', 'fern', 'large_fern',
    'snow', 'vine', 'weeping_vines', 'twisting_vines',
])

let isExploring = false
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

function _isSurfaceLike(bot, pos) {
    const feet = pos ?? bot.entity.position.floored()
    const sky = _skyLightAt(bot, feet)
    const head = bot.blockAt(feet.offset(0, 1, 0))
    const above = bot.blockAt(feet.offset(0, 2, 0))
    return sky >= 14 && _isPassable(head?.name) && _isPassable(above?.name)
}

function _candidateScore(bot, pos) {
    return bot.entity.position.distanceTo(pos.offset(0.5, 0, 0.5))
}

async function _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms))
}

function _countNearbyLogs(bot, pos, radius = 24) {
    try {
        const matches = bot.findBlocks({
            point: pos,
            matching: b => b?.name && b.name.endsWith('_log'),
            maxDistance: radius,
            count: 40,
        })
        return matches?.length ?? 0
    } catch (_) {
        return 0
    }
}

function _findSurfaceSpotNear(bot, center, searchRadius = 8) {
    if (bot.game?.dimension && bot.game.dimension !== 'overworld') {
        return null
    }

    const base = center.floored ? center.floored() : center
    const minY = _worldMinY(bot)
    const maxY = _worldMaxY(bot)
    const startY = Math.max(base.y, minY + 1)
    const candidates = []

    for (let r = 0; r <= searchRadius; r++) {
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
                    if (_skyLightAt(bot, feet.position) < 14) continue

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

function findExploreSpotForTrees(bot, maxRadius = 96, minRadius = 12, treeRadius = 24) {
    const base = bot.entity.position.floored()
    const logPositions = bot.findBlocks({
        point: base,
        matching: b => b?.name && b.name.endsWith('_log'),
        maxDistance: maxRadius,
        count: 80,
    }) ?? []

    const filteredLogs = logPositions
        .filter(pos => pos.distanceTo(base) >= minRadius)
        .sort((a, b) => a.distanceTo(base) - b.distanceTo(base))

    for (const logPos of filteredLogs) {
        const target = _findSurfaceSpotNear(bot, logPos, 8)
        if (!target) continue
        const nearbyLogs = _countNearbyLogs(bot, target, treeRadius)
        if (nearbyLogs > 0) {
            return target
        }
    }

    return null
}

activityStack.register('explore', _pause)

function _pause(_bot) {
    isExploring = false
    _isPaused = true
    console.log('[Explore] 暫停探索')
}

async function startExploring(bot, goal = {}) {
    if (isExploring) {
        console.log('[Explore] 已在探索中')
        return
    }
    isExploring = true
    _isPaused = false
    _runToken += 1
    activityStack.push(bot, 'explore', goal, (b) => _resumeExploring(b, goal))
    console.log(`[Explore] 開始探索 target=${goal.target ?? 'unknown'}`)
    _run(bot, goal, _runToken)
}

function _resumeExploring(bot, goal) {
    if (isExploring) return
    isExploring = true
    _isPaused = false
    _runToken += 1
    console.log(`[Explore] 恢復探索 target=${goal.target ?? 'unknown'}`)
    _run(bot, goal, _runToken)
}

function stopExploring(bot) {
    if (!isExploring) return
    isExploring = false
    _isPaused = false
    _runToken += 1
    try {
        bot.pathfinder?.setGoal(null)
    } catch (_) {}
    try {
        bot.clearControlStates?.()
    } catch (_) {}
    console.log('[Explore] 停止探索')
}

async function _run(bot, goal = {}, token) {
    try {
        const targetType = goal.target ?? 'trees'
        if (targetType !== 'trees') {
            bridge.sendState(bot, 'activity_stuck', {
                activity_name: 'explore',
                reason: 'timeout',
                detail: `尚未支援的探索目標: ${targetType}`,
            })
            return
        }

        const target = findExploreSpotForTrees(
            bot,
            Number.isFinite(goal.radius) ? goal.radius : 96,
            Number.isFinite(goal.minRadius) ? goal.minRadius : 12,
            Number.isFinite(goal.scanRadius) ? goal.scanRadius : 24,
        )

        if (!target || !isExploring || token !== _runToken) {
            bridge.sendState(bot, 'activity_stuck', {
                activity_name: 'explore',
                reason: 'timeout',
                detail: '找不到附近有樹的可站立地表區域',
            })
            return
        }

        try {
            bot.pathfinder?.setGoal(null)
        } catch (_) {}
        console.log(`[Explore] 傳送到探索點 (${target.x}, ${target.y}, ${target.z})`)
        noteTeleportLikeAction()
        bot.chat(`/tp ${bot.username} ${target.x} ${target.y} ${target.z}`)
        await _sleep(500)
        if (token !== _runToken || !isExploring) return

        const pos = bot.entity.position
        const arrived = Math.abs(pos.x - target.x) <= 1
            && Math.abs(pos.y - target.y) <= 1
            && Math.abs(pos.z - target.z) <= 1
        const nearbyLogs = _countNearbyLogs(bot, bot.entity.position.floored(), goal.scanRadius ?? 24)

        if (arrived && _isSurfaceLike(bot) && nearbyLogs > 0) {
            console.log(`[Explore] 已找到 trees 區域（附近 ${nearbyLogs} 個 log）`)
            bridge.sendState(bot, 'activity_done', {
                activity: 'explore',
                reason: 'goal_reached',
                found: targetType,
            })
            return
        }

        bridge.sendState(bot, 'activity_stuck', {
            activity_name: 'explore',
            reason: 'timeout',
            detail: '已移動到新區域，但附近仍未找到可用樹木',
        })
    } catch (e) {
        if (token !== _runToken || !isExploring) return
        console.log(`[Explore] 失敗: ${e.message}`)
        bridge.sendState(bot, 'activity_stuck', {
            activity_name: 'explore',
            reason: 'timeout',
            detail: e.message,
        })
    } finally {
        if (token !== _runToken) return
        const paused = _isPaused
        isExploring = false
        _isPaused = false
        if (!paused) activityStack.pop(bot)
    }
}

module.exports = {
    startExploring,
    stopExploring,
    findExploreSpotForTrees,
}
