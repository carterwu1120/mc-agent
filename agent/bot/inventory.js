const bridge = require('./bridge')
const { isActive: isFishing, stopFishing, startFishing } = require('./fishing')

const INVENTORY_FULL = 36

let _decision = null
let _checking = false
let _wasFishing = false

function applyInventoryDecision(decision) {
    _decision = decision
}

async function _handleFull(bot) {
    if (_checking) return
    _checking = true

    // 暫停當前行為
    _wasFishing = isFishing()
    if (_wasFishing) stopFishing(bot)

    console.log('[Inv] 背包已滿，詢問 LLM...')

    bridge.sendState(bot, 'inventory_full', {})

    // 等待 LLM 決策（最多 30 秒）
    const decision = await _waitForDecision(30000)

    if (!decision || decision.action === 'continue') {
        console.log('[Inv] LLM 決定繼續（或超時）')
    } else if (decision.action === 'drop') {
        const toDrop = new Set(decision.items ?? [])
        for (const item of bot.inventory.items()) {
            if (toDrop.has(item.name)) {
                await bot.tossStack(item)
                console.log(`[Inv] 丟棄 ${item.name} x${item.count}`)
            }
        }
    }

    // 恢復之前的行為
    if (_wasFishing) {
        console.log('[Inv] 恢復釣魚')
        startFishing(bot)
    }

    _checking = false
}

function _waitForDecision(timeoutMs) {
    return new Promise((resolve) => {
        const check = setInterval(() => {
            if (_decision !== null) {
                clearInterval(check)
                clearTimeout(timer)
                const d = _decision
                _decision = null
                resolve(d)
            }
        }, 200)
        const timer = setTimeout(() => {
            clearInterval(check)
            resolve(null)
        }, timeoutMs)
    })
}

function startMonitor(bot) {
    bot.on('playerCollect', (collector) => {
        if (collector.username !== bot.username) return
        const slots = bot.inventory.items().length
        if (slots >= INVENTORY_FULL) {
            _handleFull(bot)
        }
    })
    console.log('[Inv] 背包監控已啟動')
}

module.exports = { startMonitor, applyInventoryDecision }
