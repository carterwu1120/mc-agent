const { Vec3 } = require('vec3')
const bridge = require('./bridge')
const { ensureToolFor } = require('./crafting')
const { compactCompressibleItems } = require('./crafting')
const { markBuried } = require('./buried')
const activityStack = require('./activity')

const INVENTORY_FULL = 36

let _decision = null
let _checking = false

function applyInventoryDecision(decision) {
    _decision = decision
}

async function _handleFull(bot) {
    return _tidyInventory(bot, { forceLlm: true })
}

async function _tidyInventory(bot, { forceLlm = false } = {}) {
    if (_checking) return
    _checking = true

    // 暫停當前行為
    activityStack.pause(bot)

    const beforeSlots = bot.inventory.items().length
    const compacted = await compactCompressibleItems(bot)
    const afterSlots = bot.inventory.items().length
    if (compacted > 0) {
        console.log(`[Inv] 已壓縮 ${compacted} 組資源方塊，背包格數 ${beforeSlots} -> ${afterSlots}`)
    }
    if (!forceLlm || afterSlots < INVENTORY_FULL) {
        console.log('[Inv] 壓縮後背包已有空間，不需丟棄物品')
        activityStack.resumeCurrent(bot)
        _checking = false
        return
    }

    console.log('[Inv] 背包已滿，詢問 LLM...')

    bridge.sendState(bot, 'inventory_full', {})

    // 等待 LLM 決策（最多 30 秒）
    const decision = await _waitForDecision(30000)

    if (!decision || decision.action === 'continue') {
        console.log('[Inv] LLM 決定繼續（或超時）')
    } else if (decision.action === 'drop') {
        // LLM 只回名稱清單，全部丟光（count = null）
        const toDrop = new Map((decision.items ?? []).map(name => [name, null]))
        await _buryItems(bot, toDrop)
    }

    // 恢復之前的行為
    activityStack.resumeCurrent(bot)
    _checking = false
}

async function _buryItems(bot, itemsMap) {
    const originalYaw   = bot.entity.yaw
    const originalPitch = bot.entity.pitch

    // 先停止 pathfinder，防止之後走進洞
    bot.pathfinder.setGoal(null)

    // 1. 轉向正後方
    const backYaw = originalYaw + Math.PI
    await bot.look(backYaw, 0)

    // 2. 計算挖洞位置：地板高度往下兩格
    //    feet = bot 腳部所在格（通常是空氣），地板 = feet - 1
    // snap 到最近的基本方向（N/S/E/W），避免斜角導致 dx/dz 同時為 ±1
    const snapped = Math.round(backYaw / (Math.PI / 2)) * (Math.PI / 2)
    const dx = Math.round(-Math.sin(snapped))
    const dz = Math.round(-Math.cos(snapped))
    const feet = bot.entity.position.floored()
    const clearPos  = feet.offset(dx,  0, dz)   // bot 腳部高度的前方格
    const clearPos2 = feet.offset(dx,  1, dz)   // bot 頭部高度的前方格
    const topPos    = feet.offset(dx, -1, dz)   // 地板高度的前方格（封口位置）
    const botPos    = feet.offset(dx, -2, dz)   // 地板下一格（物品落點）

    // 3. 清出丟物路徑：正前方腳/頭高度
    const pathToClear = [
        clearPos2,                                      // 頭部高度正前方
        clearPos,                                       // 腳部高度正前方
    ]
    for (const pos of pathToClear) {
        const b = bot.blockAt(pos)
        if (b && b.boundingBox === 'block') {
            try { await ensureToolFor(bot, b.name); await bot.dig(b) } catch (_) {}
        }
    }

    const topBlock = bot.blockAt(topPos)
    if (topBlock && topBlock.boundingBox === 'block') {
        try { await ensureToolFor(bot, topBlock.name); await bot.dig(topBlock) } catch (_) {}
    }
    const botBlock = bot.blockAt(botPos)
    if (botBlock && botBlock.boundingBox === 'block') {
        try { await ensureToolFor(bot, botBlock.name); await bot.dig(botBlock) } catch (_) {}
    }

    // 4. 丟垃圾進洞（稍微仰角讓物品拋物線落入洞中）
    //    封口材料（cobblestone 等）至少預留 1 個，避免丟完沒東西封口
    const FILL_BLOCKS = ['cobblestone', 'dirt', 'sand', 'netherrack', 'stone']
    await bot.look(backYaw, -0.3)
    for (const item of bot.inventory.items()) {
        if (!itemsMap.has(item.name)) continue
        const isFill = FILL_BLOCKS.includes(item.name)
        const requested = itemsMap.get(item.name) ?? item.count
        const tossCount = Math.min(requested, isFill ? Math.max(0, item.count - 1) : item.count)
        if (tossCount <= 0) continue
        await bot.toss(item.type, null, tossCount)
        console.log(`[Inv] 埋棄 ${item.name} x${tossCount}`)
    }
    await _sleep(600)  // 等物品落入洞底

    // 5. 如果丟出去的東西被吸回背包，重丟一次
    const reabsorbed = bot.inventory.items().filter(i => itemsMap.has(i.name))
    if (reabsorbed.length > 3) {
        console.log(`[Inv] ${reabsorbed.length} 種物品被吸回，重丟`)
        await bot.look(backYaw, -0.5)
        for (const item of reabsorbed) {
            const isFill = FILL_BLOCKS.includes(item.name)
            const requested = itemsMap.get(item.name) ?? item.count
            const tossCount = Math.min(requested, isFill ? Math.max(0, item.count - 1) : item.count)
            if (tossCount <= 0) continue
            try { await bot.toss(item.type, null, tossCount) } catch (_) {}
            console.log(`[Inv] 重丟 ${item.name} x${tossCount}`)
        }
        await _sleep(600)
    }

    // 7. 封口：
    //    botPos 若成功挖開 → 物品在 botPos，封 topPos
    //    botPos 挖不動（基岩等）→ 物品在 topPos，改封 clearPos
    const botAfter = bot.blockAt(botPos)
    const sealPos = (botAfter && botAfter.boundingBox !== 'block') ? topPos : clearPos

    const fillItem = bot.inventory.items().find(i => FILL_BLOCKS.includes(i.name))
    if (fillItem) {
        let sealed = false
        for (const [ox, oy, oz] of [[1,0,0],[-1,0,0],[0,1,0],[0,0,1],[0,0,-1],[0,-1,0]]) {
            const ref = bot.blockAt(sealPos.offset(ox, oy, oz))
            if (!ref || ref.boundingBox !== 'block') continue
            const faceCenter = ref.position.offset(0.5 - ox * 0.5, 0.5 - oy * 0.5, 0.5 - oz * 0.5)
            try {
                await bot.equip(fillItem, 'hand')
                await bot.lookAt(faceCenter)
                await bot.placeBlock(ref, new Vec3(-ox, -oy, -oz))
                markBuried(sealPos)
                markBuried(botPos)
                console.log('[Inv] 垃圾已埋入地下')
                sealed = true
                break
            } catch (_) {}
        }
        if (!sealed) console.log('[Inv] 無法封閉洞口')
    } else {
        console.log('[Inv] 沒有填埋材料，洞口未封閉')
    }

    // 8. 轉回正面
    await bot.look(originalYaw, originalPitch)
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
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

module.exports = {
    startMonitor,
    applyInventoryDecision,
    buryItems: _buryItems,
    tidyInventory: _tidyInventory,
    handleFull: _handleFull,
}
