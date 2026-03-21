const { goals } = require('mineflayer-pathfinder')
const { findNearestEntity, findNearestWater, findFishableWater, isWater } = require('./world')

let isFishing = false
let _lastWaterKey = null      // 上次目標水面的 key
let _savedPitch = null        // _faceTarget 使用的 pitch（含臨時調整）
let _confirmedPitch = null    // 上次成功落水的 pitch（失敗時從此基準計算）

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
    _loop(bot)
}

function stopFishing(bot) {
    if (!isFishing) return
    isFishing = false
    if (bot.fishing) bot.activateItem()
    console.log('[Fish] 停止釣魚')
}

// pitch 範圍限制（弧度）：Mineflayer 負值=往下看，正值=往上看
const PITCH_MIN = -1.0   // ~-57°，幾乎垂直往下
const PITCH_MAX = 0.5    // ~+28°，往上

async function _loop(bot) {
    let failStreak = 0  // 連續失敗次數

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
            break
        }
        console.log(`[Fish] 目標水面 (${water.position.x}, ${water.position.y}, ${water.position.z})`)
        await _faceTarget(bot, water.position)

        await bot.activateItem()
        console.log(`[Fish] 拋竿 (yaw=${(bot.entity.yaw * 57.3).toFixed(1)}° pitch=${(bot.entity.pitch * 57.3).toFixed(1)}°)`)

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

            const tooFar = bot.entity.position.distanceTo(water.position) > 8
            if (failStreak >= 3 || tooFar) {
                console.log('[Fish] 換位置')
                _savedPitch = null
                await _walkToWater(bot)
                failStreak = 0
            } else {
                // 從上次確認有效的 pitch 往上偏移，不累加
                const tryPitch = (_confirmedPitch ?? 0) + 0.15 * failStreak
                if (tryPitch > PITCH_MAX) {
                    console.log('[Fish] 仰角已到極限，換位置')
                    _savedPitch = null
                    _confirmedPitch = null
                    await _walkToWater(bot)
                    failStreak = 0
                } else {
                    console.log(`[Fish] 調整仰角至 ${(tryPitch * 57.3).toFixed(1)}°`)
                    _savedPitch = tryPitch  // 讓下次 _faceTarget 使用調整後的角度
                    await bot.look(bot.entity.yaw, tryPitch, true)
                    await _sleep(300)
                }
            }
            continue
        }

        // 成功落水，記住這個 pitch 作為基準（用 _savedPitch 而非 bot.entity.pitch，避免伺服器覆蓋）
        _confirmedPitch = _savedPitch ?? 0
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

function _waitForBite(bot, bobber, timeoutMs = 30000) {
    return new Promise((resolve) => {
        const startY = bobber.position.y

        const onMove = (entity) => {
            if (entity.id !== bobber.id) return
            // 下沉 0.3 格以上才算真正魚咬（避免入水晃動誤判）
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

// 面向目標位置：先 lookAt 取得正確 yaw，再把 pitch 換成仰角
// 同一目標重用上次調整過的 pitch，避免每次 loop 重置
async function _faceTarget(bot, targetPos) {
    await bot.lookAt(targetPos)

    const waterKey = `${Math.floor(targetPos.x)},${Math.floor(targetPos.y)},${Math.floor(targetPos.z)}`
    let pitch
    if (_savedPitch !== null && waterKey === _lastWaterKey) {
        pitch = _savedPitch  // 同目標，沿用上次有效角度
    } else {
        _lastWaterKey = waterKey
        _savedPitch = null
        _confirmedPitch = null
        pitch = 0  // 從水平開始，由 bad_angle recovery 往上調整
    }

    pitch = Math.max(PITCH_MIN, Math.min(PITCH_MAX, pitch))
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

module.exports = { startFishing, stopFishing }
