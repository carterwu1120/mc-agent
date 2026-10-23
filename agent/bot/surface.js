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
                    if (!isPassableSpace(feet) || !isPassableSpace(head)) continue
                    if (!isSafeGround(ground)) continue

                    const light = skyLightAt(bot, feet.position)
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
                reason: 'timeout',
                detail: '找不到可靠的可站立地表位置',
            })
            return
        }

        _setEscapeMovements(bot)
        bot.pathfinder?.setGoal(null)
        console.log(`[Surface] 傳送到地表 (${target.x}, ${target.y}, ${target.z})`)
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
