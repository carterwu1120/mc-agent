const { goals } = require('mineflayer-pathfinder')
const { findNearestEntity, findNearestWater, findFishableWater, isWater, scanAreaMap } = require('./world')
const bridge = require('./bridge')
const { setActivity } = require('./activity')

let isFishing = false
let _savedPitch = null   // 目前使用的 pitch（同目標時沿用）
let _lastWaterKey = null
let _llmDecision = null  // 待處理的 LLM 決策（由 applyLLMDecision 設定）

const PITCH_MIN = -1.0   // ~-57°
const PITCH_MAX = 0.5    // ~+28°

async function startFishing(bot) {
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
    setActivity('fishing')
    _loop(bot)
}

function stopFishing(bot) {
    if (!isFishing) return
    isFishing = false
    setActivity('idle')
    if (bot.fishing) bot.activateItem()
    console.log('[Fish] 停止釣魚')
}

async function _loop(bot) {
    let failStreak = 0

    while (isFishing) {
        let water = findFishableWater(bot, 16)
        if (!water) {
            console.log('[Fish] 找不到合適的拋竿位置，走向水邊')
            await _walkToWater(bot)
            water = findFishableWater(bot, 16)
        }
        if (!water) {
            console.log('[Fish] 附近沒有水，停止釣魚')
            isFishing = false
            setActivity('idle')
            break
        }

        console.log(`[Fish] 目標水面 (${water.position.x}, ${water.position.y}, ${water.position.z})`)
        await _faceTarget(bot, water.position)

        await bot.activateItem()
        console.log(`[Fish] 拋竿 (yaw=${(bot.entity.yaw * 57.3).toFixed(1)}° pitch=${((_savedPitch ?? 0) * 57.3).toFixed(1)}°)`)

        await _sleep(2000)

        const bobber = findNearestEntity(bot, 'fishing_bobber')
        if (!bobber) {
            console.log('[Fish] 找不到浮標，重試')
            continue
        }

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

            // 角度試完還是失敗 → 詢問 LLM
            console.log('[Fish] 角度調整無效，詢問 LLM...')
            bridge.sendState(bot, 'fishing_stuck', {
                waterTarget: water.position,
                areaMap: scanAreaMap(bot, 10),
            })

            const decision = await _waitForLLMDecision(20000)
            if (!decision) {
                console.log('[Fish] LLM 超時，走向水邊')
                _savedPitch = null
                await _walkToWater(bot)
                failStreak = 0
                continue
            }

            if (decision.action === 'stop') {
                console.log('[Fish] LLM 決定停止釣魚')
                isFishing = false
                setActivity('idle')
                break
            }

            if (decision.action === 'move') {
                const pos = bot.entity.position
                console.log(`[Fish] LLM 決定移動至 (${decision.x}, ${decision.z})`)
                _savedPitch = null
                try {
                    await bot.pathfinder.goto(new goals.GoalNear(decision.x, pos.y, decision.z, 2))
                } catch (e) { /* 找不到路就算了 */ }
                await _sleep(500)
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
        } else {
            console.log('[Fish] 超時，重新拋竿')
            await _sleep(500)
        }
    }
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
    const water = findNearestWater(bot, 32)
    if (!water) return
    console.log(`[Fish] 走向水邊 (${water.position.x}, ${water.position.y}, ${water.position.z})`)
    await bot.pathfinder.goto(new goals.GoalNear(water.position.x, water.position.y, water.position.z, 3))
    await _sleep(500)
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
