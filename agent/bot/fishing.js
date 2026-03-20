let isFishing = false

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
    if (bot.fishing) bot.activateItem()  // 收竿
    console.log('[Fish] 停止釣魚')
}

async function _loop(bot) {
    while (isFishing) {
        // 拋竿
        await bot.activateItem()
        console.log('[Fish] 拋竿')

        // 等浮標出現
        await _sleep(1000)

        // 找浮標
        const bobber = _findBobber(bot)
        if (!bobber) {
            console.log('[Fish] 找不到浮標，重試')
            continue
        }

        // 等魚上鉤（浮標 Y 軸下沉）或超時
        const bitten = await _waitForBite(bot, bobber)

        if (!isFishing) break

        // 收竿
        await bot.activateItem()
        console.log(bitten ? '[Fish] 收竿！釣到東西了' : '[Fish] 超時，重新拋竿')

        await _sleep(500)
    }
}

function _findBobber(bot) {
    return Object.values(bot.entities).find(e =>
        (e.name === 'fishing_bobber' || e.name === 'fishing_hook') &&
        e.position.distanceTo(bot.entity.position) < 32
    )
}

function _waitForBite(bot, bobber, timeoutMs = 30000) {
    return new Promise((resolve) => {
        const startY = bobber.position.y

        const onMove = (entity) => {
            if (entity.id !== bobber.id) return
            // 浮標 Y 軸下沉超過 0.1 = 魚上鉤
            if (entity.position.y < startY - 0.1) {
                cleanup()
                resolve(true)
            }
        }

        const timer = setTimeout(() => {
            cleanup()
            resolve(false)  // 超時
        }, timeoutMs)

        const cleanup = () => {
            bot.removeListener('entityMoved', onMove)
            clearTimeout(timer)
        }

        bot.on('entityMoved', onMove)
    })
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

module.exports = { startFishing, stopFishing }
