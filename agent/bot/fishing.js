const { goals } = require('mineflayer-pathfinder')
const { findNearestEntity, findNearestWater, findFishableWater, isWater, scanAreaMap } = require('./world')
const bridge = require('./bridge')
const activityStack = require('./activity')

let isFishing = false
let _isPaused = false
let _catches = 0
let _savedPitch = null   // 目前使用的 pitch（同目標時沿用）
let _lastWaterKey = null
let _llmDecision = null  // 待處理的 LLM 決策（由 applyLLMDecision 設定）

const PITCH_MIN = -1.0   // ~-57°
const PITCH_MAX = 0.5    // ~+28°

activityStack.register('fishing', _pause)

function _pause(bot) {
    isFishing = false
    _isPaused = true
    if (bot.fishing) bot.activateItem()
    console.log('[Fish] 暫停釣魚')
}

async function startFishing(bot, goal = {}) {
    if (isFishing) {
        console.log('[Fish] 已在釣魚中')
        return
    }

    const rod = bot.inventory.items().find(i => i.name === 'fishing_rod')
    if (!rod) {
        console.log('[Fish] 背包裡沒有釣竿')
        return
    }

    await bot.equip(rod, 'hand')
    console.log('[Fish] 釣竿已裝備，開始釣魚')

    isFishing = true
    _catches = 0
    activityStack.push(bot, 'fishing', goal, (b) => _resumeFishing(b, goal))
    _loop(bot, goal)
}

function _resumeFishing(bot, originalGoal) {
    if (isFishing) return
    const remainingCatches = originalGoal.catches
        ? Math.max(1, originalGoal.catches - _catches)
        : undefined
    isFishing = true
    activityStack.updateTopGoal(remainingCatches
        ? { ...originalGoal, catches: remainingCatches }
        : originalGoal)
    const rod = bot.inventory.items().find(i => i.name === 'fishing_rod')
    if (rod) bot.equip(rod, 'hand').catch(() => {})
    console.log('[Fish] 恢復釣魚')
    _loop(bot, originalGoal)
}

function stopFishing(bot) {
    if (!isFishing) return
    isFishing = false
    _isPaused = false
    if (bot.fishing) bot.activateItem()
    console.log('[Fish] 停止釣魚')
}

async function _loop(bot, goal = {}) {
    _isPaused = false
    let failStreak = 0
    let noBobberStreak = 0
    const startTime = Date.now()

    while (isFishing) {
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Fish] 達到時間目標 ${goal.duration}s，停止`)
            isFishing = false
            bridge.sendState(bot, 'activity_done', { activity: 'fishing', reason: 'goal_reached' })
            break
        }
        if (goal.catches && _catches >= goal.catches) {
            console.log(`[Fish] 達到釣魚目標 ${goal.catches} 次，停止`)
            isFishing = false
            bridge.sendState(bot, 'activity_done', { activity: 'fishing', reason: 'goal_reached' })
            break
        }
        let water = findFishableWater(bot, 16)
        if (!water) {
            console.log('[Fish] 找不到合適的拋竿位置，走向水邊')
            await _walkToWater(bot)
            water = findFishableWater(bot, 16)
        }
        if (!water) {
            console.log('[Fish] 附近沒有水，停止釣魚')
            isFishing = false
            break
        }

        console.log(`[Fish] 目標水面 (${water.position.x}, ${water.position.y}, ${water.position.z})`)
        await _faceTarget(bot, water.position)

        await bot.activateItem()
        console.log(`[Fish] 拋竿 (yaw=${(bot.entity.yaw * 57.3).toFixed(1)}° pitch=${((_savedPitch ?? 0) * 57.3).toFixed(1)}°)`)

        await _sleep(2000)

        const bobber = findNearestEntity(bot, 'fishing_bobber')
        if (!bobber) {
            noBobberStreak++
            console.log(`[Fish] 找不到浮標（第 ${noBobberStreak} 次）`)
            if (noBobberStreak >= 3) {
                const repositioned = await _tryLocalReposition(bot)
                if (repositioned) {
                    noBobberStreak = 0
                    failStreak = 0
                    continue
                }
                const handled = await _handleFishingStuck(bot, 'no_bobber', water)
                if (!handled) {
                    console.log('[Fish] LLM 超時，走向水邊')
                    _savedPitch = null
                    await _walkToWater(bot)
                }
                noBobberStreak = 0
                failStreak = 0
            } else {
                await _sleep(500)
            }
            continue
        }
        noBobberStreak = 0

        if (!isWater(bot, bobber.position)) {
            await bot.activateItem()
            failStreak++
            console.log(`[Fish] 拋竿未落水（第 ${failStreak} 次）`)

            // 先嘗試調整角度（最多 3 次）
            const tryPitch = (_savedPitch ?? 0) + 0.15 * failStreak
            if (failStreak < 3 && tryPitch <= PITCH_MAX) {
                console.log(`[Fish] 調整仰角至 ${(tryPitch * 57.3).toFixed(1)}°`)
                _savedPitch = tryPitch
                await bot.look(bot.entity.yaw, tryPitch, true)
                await _sleep(300)
                continue
            }

            // 角度試完還是失敗 → 統一走 activity_stuck
            const repositioned = await _tryLocalReposition(bot)
            if (repositioned) {
                failStreak = 0
                continue
            }
            const handled = await _handleFishingStuck(bot, 'bad_cast', water)
            if (!handled) {
                console.log('[Fish] LLM 超時，走向水邊')
                _savedPitch = null
                await _walkToWater(bot)
                failStreak = 0
                continue
            }
            failStreak = 0
            continue
        }

        // 成功落水
        failStreak = 0
        const bitten = await _waitForBite(bot, bobber)
        if (!isFishing) break

        const before = new Map()
        for (const item of bot.inventory.items()) {
            before.set(item.name, (before.get(item.name) ?? 0) + item.count)
        }
        await bot.activateItem()

        if (bitten) {
            await _sleep(1000)
            const after = new Map()
            for (const item of bot.inventory.items()) {
                after.set(item.name, (after.get(item.name) ?? 0) + item.count)
            }
            const caught = [...after.entries()]
                .filter(([name, count]) => count > (before.get(name) ?? 0))
                .map(([name, count]) => `${name} x${count - (before.get(name) ?? 0)}`)
            console.log(caught.length > 0
                ? `[Fish] 收竿！釣到：${caught.join(', ')}`
                : '[Fish] 收竿！（物品未進背包）')
            _catches++
            activityStack.updateProgress({ catches: _catches })
        } else {
            console.log('[Fish] 超時，重新拋竿')
            await _sleep(500)
        }
    }

    if (!_isPaused) activityStack.pop(bot)
    _isPaused = false
}

async function _handleFishingStuck(bot, reason, water) {
    console.log(`[Fish] ${reason}，送出 activity_stuck...`)
    bridge.sendState(bot, 'activity_stuck', {
        activity: 'fishing',
        reason,
        waterTarget: water.position,
        areaMap: scanAreaMap(bot, 10),
    })

    const decision = await _waitForLLMDecision(20000)
    if (!decision) return false

    if (decision.action === 'stop') {
        console.log('[Fish] LLM 決定停止釣魚')
        isFishing = false
        return true
    }

    if (decision.action === 'move') {
        console.log(`[Fish] LLM 決定移動至 (${decision.x}, ${decision.z})`)
        _savedPitch = null
        const stand = _resolveStandPosition(bot, decision.x, decision.z)
        if (!stand) {
            console.log('[Fish] 找不到 LLM 目標附近可站位置')
            return false
        }
        try {
            await bot.pathfinder.goto(new goals.GoalNear(stand.x, stand.y, stand.z, 1))
        } catch (e) {
            console.log(`[Fish] LLM 移動失敗: ${e.message}`)
            return false
        }
        await _sleep(500)
    }

    return true
}

// 等待 LLM 決策（polling _llmDecision，有 timeout）
function _waitForLLMDecision(timeoutMs = 20000) {
    return new Promise((resolve) => {
        const check = setInterval(() => {
            if (_llmDecision !== null) {
                clearInterval(check)
                clearTimeout(timer)
                const d = _llmDecision
                _llmDecision = null
                resolve(d)
            }
        }, 200)
        const timer = setTimeout(() => {
            clearInterval(check)
            resolve(null)
        }, timeoutMs)
    })
}

function _waitForBite(bot, bobber, timeoutMs = 30000) {
    return new Promise((resolve) => {
        const startY = bobber.position.y

        const onMove = (entity) => {
            if (entity.id !== bobber.id) return
            if (entity.position.y < startY - 0.3) {
                cleanup()
                resolve(true)
            }
        }

        const timer = setTimeout(() => {
            cleanup()
            resolve(false)
        }, timeoutMs)

        const cleanup = () => {
            bot.removeListener('entityMoved', onMove)
            clearTimeout(timer)
        }

        bot.on('entityMoved', onMove)
    })
}

// 面向目標水面：lookAt 取得正確 yaw，pitch 同目標時沿用，否則重置
async function _faceTarget(bot, targetPos) {
    await bot.lookAt(targetPos)
    const waterKey = `${Math.floor(targetPos.x)},${Math.floor(targetPos.y)},${Math.floor(targetPos.z)}`
    if (_lastWaterKey !== waterKey) {
        _lastWaterKey = waterKey
        _savedPitch = null
    }
    const pitch = Math.max(PITCH_MIN, Math.min(PITCH_MAX, _savedPitch ?? 0))
    await bot.look(bot.entity.yaw, pitch, true)
}

async function _walkToWater(bot) {
    const stand = _findNearbyFishingStand(bot, 10)
    if (stand) {
        console.log(`[Fish] 走向本地釣魚站位 (${stand.x}, ${stand.y}, ${stand.z})`)
        try {
            await bot.pathfinder.goto(new goals.GoalNear(stand.x, stand.y, stand.z, 1))
            await _sleep(500)
            return
        } catch (e) {
            console.log(`[Fish] 前往本地釣魚站位失敗: ${e.message}`)
        }
    }
    const water = findNearestWater(bot, 32)
    if (!water) return
    console.log(`[Fish] 走向水邊 (${water.position.x}, ${water.position.y}, ${water.position.z})`)
    try {
        await bot.pathfinder.goto(new goals.GoalNear(water.position.x, water.position.y, water.position.z, 3))
        await _sleep(500)
    } catch (e) {
        console.log(`[Fish] 前往水邊失敗: ${e.message}`)
    }
}

async function _tryLocalReposition(bot) {
    const stand = _findNearbyFishingStand(bot, 8)
    if (!stand) return false

    const current = bot.entity.position.floored()
    if (Math.abs(current.x - stand.x) <= 1 && Math.abs(current.z - stand.z) <= 1 && Math.abs(current.y - stand.y) <= 1) {
        return false
    }

    console.log(`[Fish] 本地重新站位至 (${stand.x}, ${stand.y}, ${stand.z})`)
    _savedPitch = null
    try {
        await bot.pathfinder.goto(new goals.GoalNear(stand.x, stand.y, stand.z, 1))
        await _sleep(400)
        return true
    } catch (_) {
        return false
    }
}

function _findNearbyFishingStand(bot, radius = 8) {
    const pos = bot.entity.position.floored()
    const candidates = []

    for (let dx = -radius; dx <= radius; dx++) {
        for (let dz = -radius; dz <= radius; dz++) {
            for (let dy = -2; dy <= 2; dy++) {
                const feetPos = pos.offset(dx, dy, dz)
                const feet = bot.blockAt(feetPos)
                const body = bot.blockAt(feetPos.offset(0, 1, 0))
                const floor = bot.blockAt(feetPos.offset(0, -1, 0))
                if (!feet || !body || !floor) continue
                if (feet.name !== 'air' || body.name !== 'air') continue
                if (floor.name === 'air' || floor.name === 'water' || floor.name === 'flowing_water') continue
                if (Math.abs(feetPos.y - pos.y) > 1) continue

                const nearWater = _hasNearbyWater(bot, feetPos, 2)
                if (!nearWater) continue

                const score =
                    feetPos.distanceTo(bot.entity.position) +
                    Math.abs(feetPos.y - pos.y) * 2 +
                    _waterDistanceScore(bot, feetPos, 3)
                candidates.push({ pos: feetPos, score })
            }
        }
    }

    candidates.sort((a, b) => a.score - b.score)
    return candidates[0]?.pos ?? null
}

function _resolveStandPosition(bot, targetX, targetZ, radius = 2) {
    const baseY = Math.floor(bot.entity.position.y)
    const candidates = []
    for (let dx = -radius; dx <= radius; dx++) {
        for (let dz = -radius; dz <= radius; dz++) {
            for (let dy = -2; dy <= 2; dy++) {
                const feetPos = bot.entity.position.floored().offset(
                    Math.floor(targetX) - Math.floor(bot.entity.position.x) + dx,
                    dy,
                    Math.floor(targetZ) - Math.floor(bot.entity.position.z) + dz
                )
                const feet = bot.blockAt(feetPos)
                const body = bot.blockAt(feetPos.offset(0, 1, 0))
                const floor = bot.blockAt(feetPos.offset(0, -1, 0))
                if (!feet || !body || !floor) continue
                if (feet.name !== 'air' || body.name !== 'air') continue
                if (floor.name === 'air' || floor.name === 'water' || floor.name === 'flowing_water') continue
                if (Math.abs(feetPos.y - baseY) > 1) continue

                const score =
                    Math.abs(feetPos.x - targetX) +
                    Math.abs(feetPos.z - targetZ) +
                    Math.abs(feetPos.y - baseY) * 2
                candidates.push({ pos: feetPos, score })
            }
        }
    }

    candidates.sort((a, b) => a.score - b.score)
    return candidates[0]?.pos ?? null
}

function _hasNearbyWater(bot, pos, radius) {
    for (let dx = -radius; dx <= radius; dx++) {
        for (let dz = -radius; dz <= radius; dz++) {
            const block = bot.blockAt(pos.offset(dx, 0, dz))
            const below = bot.blockAt(pos.offset(dx, -1, dz))
            if (block?.name === 'water' || block?.name === 'flowing_water') return true
            if ((block?.name === 'air' || block?.name === 'cave_air') && (below?.name === 'water' || below?.name === 'flowing_water')) return true
        }
    }
    return false
}

function _waterDistanceScore(bot, pos, radius) {
    let best = radius + 1
    for (let dx = -radius; dx <= radius; dx++) {
        for (let dz = -radius; dz <= radius; dz++) {
            const block = bot.blockAt(pos.offset(dx, 0, dz))
            const below = bot.blockAt(pos.offset(dx, -1, dz))
            const isNearbyWater =
                block?.name === 'water' || block?.name === 'flowing_water' ||
                ((block?.name === 'air' || block?.name === 'cave_air') && (below?.name === 'water' || below?.name === 'flowing_water'))
            if (!isNearbyWater) continue
            const dist = Math.sqrt(dx * dx + dz * dz)
            if (dist < best) best = dist
        }
    }
    return best
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

function applyLLMDecision(decision) {
    _llmDecision = decision
}

function isActive() {
    return isFishing
}

module.exports = { startFishing, stopFishing, applyLLMDecision, isActive }
