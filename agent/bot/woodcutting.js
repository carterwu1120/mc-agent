const { goals, Movements } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const { setActivity } = require('./activity')
const { ensureAxe, ensureToolFor } = require('./crafting')
const bridge = require('./bridge')

let isChopping = false

const SCAFFOLD_BLOCKS = new Set([
    'dirt', 'cobblestone', 'gravel', 'sand', 'stone',
    'oak_planks', 'spruce_planks', 'birch_planks', 'jungle_planks', 'acacia_planks',
])

const VALID_SOIL = new Set(['dirt', 'grass_block', 'podzol', 'mycelium', 'rooted_dirt'])

const LOG_TO_SAPLING = {
    oak_log:      'oak_sapling',
    spruce_log:   'spruce_sapling',
    birch_log:    'birch_sapling',
    jungle_log:   'jungle_sapling',
    acacia_log:   'acacia_sapling',
    dark_oak_log: 'dark_oak_sapling',
    mangrove_log: 'mangrove_propagule',
}

async function startChopping(bot, goal = {}) {
    if (isChopping) {
        console.log('[Wood] 已在砍樹中')
        return
    }
    isChopping = true
    setActivity('chopping')
    console.log('[Wood] 開始砍樹')
    _loop(bot, goal)
}

function stopChopping(bot) {
    if (!isChopping) return
    isChopping = false
    setActivity('idle')
    console.log('[Wood] 停止砍樹')
}

async function _loop(bot, goal = {}) {
    const skipped = new Set()  // 完全無法到達的樹根位置
    let logsCollected = 0
    const startTime = Date.now()

    while (isChopping) {
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Wood] 達到時間目標 ${goal.duration}s，停止`)
            isChopping = false
            setActivity('idle')
            bridge.sendState(bot, 'activity_done', { activity: 'chopping', reason: 'goal_reached' })
            break
        }
        if (goal.logs && logsCollected >= goal.logs) {
            console.log(`[Wood] 達到採集目標 ${goal.logs} 根木頭，停止`)
            isChopping = false
            setActivity('idle')
            bridge.sendState(bot, 'activity_done', { activity: 'chopping', reason: 'goal_reached' })
            break
        }
        // 每次迴圈檢查斧頭（斧頭壞掉或第一次有材料時自動合成）
        if (!bot.inventory.items().some(i => i.name.endsWith('_axe'))) {
            const ok = await ensureAxe(bot)
            if (!ok) {
                // 沒斧頭也繼續，用徒手砍
                console.log('[Wood] 沒有斧頭，用徒手繼續')
            }
        } else {
            const axe = bot.inventory.items().find(i => i.name.endsWith('_axe'))
            if (axe) await bot.equip(axe, 'hand')
        }

        const candidates = bot.findBlocks({
            matching: b => b.name && b.name.endsWith('_log'),
            maxDistance: 32,
            count: 20,
        })

        const rootPos = candidates.find(p => !skipped.has(_posKey(p)))

        if (!rootPos) {
            if (skipped.size > 0) {
                skipped.clear()
                continue
            }
            console.log('[Wood] 附近找不到木頭，停止')
            isChopping = false
            setActivity('idle')
            break
        }

        // BFS 找整棵樹的所有 log，由下往上排
        const treeBlocks = _findTreeLogs(bot, rootPos)
        const treeLogName = bot.blockAt(treeBlocks[0])?.name ?? null
        console.log(`[Wood] 找到樹（${treeLogName}），共 ${treeBlocks.length} 個木頭`)

        const groundY = Math.floor(bot.entity.position.y)
        let reachedAny = false

        for (const pos of treeBlocks) {
            if (!isChopping) return

            const block = bot.blockAt(pos)
            if (!block || !block.name.endsWith('_log')) continue

            const movements = new Movements(bot)
            movements.canDig = true
            bot.pathfinder.setMovements(movements)

            // 先水平走近（不疊方塊）
            try {
                await bot.pathfinder.goto(new goals.GoalNear(pos.x, bot.entity.position.y, pos.z, 3))
            } catch (e) { /* 走不到也繼續，試試疊方塊 */ }

            // 如果目標比現在高超過 2 格，手動疊方塊上去
            const heightDiff = pos.y - Math.floor(bot.entity.position.y)
            if (heightDiff > 2) {
                await _pillarUp(bot, pos.y, pos)
            }

            reachedAny = true

            if (!isChopping) return

            // 疊方塊爬上去後重新裝備斧頭
            const axe = bot.inventory.items().find(i => i.name.endsWith('_axe'))
            if (axe) await bot.equip(axe, 'hand')

            const fresh = bot.blockAt(pos)
            if (!fresh || !fresh.name.endsWith('_log')) continue

            try {
                await bot.dig(fresh)
                logsCollected++
                console.log(`[Wood] 砍下 ${fresh.name} at y=${pos.y}（共 ${logsCollected} 根）`)
                await _sleep(500)
                await _collectNearby(bot, pos, 4)
            } catch (e) {
                console.log('[Wood] 砍樹失敗:', e.message)
                await _sleep(400)
            }
        }

        // 整棵樹一個都到不了，才把 rootPos 標記為跳過
        if (!reachedAny) {
            skipped.add(_posKey(rootPos))
            continue
        }

        // 整棵樹砍完後才回收疊腳方塊、安全下來
        if (Math.floor(bot.entity.position.y) > groundY) {
            await _reclaimScaffold(bot, groundY)
        }
        if (Math.floor(bot.entity.position.y) > groundY) {
            const downMovements = new Movements(bot)
            downMovements.canDig = true
            bot.pathfinder.setMovements(downMovements)
            const botPos = bot.entity.position
            try {
                await bot.pathfinder.goto(
                    new goals.GoalNear(Math.floor(botPos.x), groundY, Math.floor(botPos.z), 2)
                )
            } catch (e) {
                console.log('[Wood] 無法安全下來，繼續')
            }
        }

        // 砍完後：撿附近掉落物、種樹苗
        await _collectNearby(bot, rootPos, 8)
        await _plantSapling(bot, rootPos, treeLogName)
    }
}

// BFS 找所有相連的 log block（同一棵樹），由下往上排序
function _findTreeLogs(bot, rootPos) {
    const visited = new Set()
    const queue = [rootPos]
    const result = []

    while (queue.length > 0) {
        const pos = queue.shift()
        const key = _posKey(pos)
        if (visited.has(key)) continue
        visited.add(key)

        const block = bot.blockAt(pos)
        if (!block || !block.name.endsWith('_log')) continue

        result.push(pos)

        const offsets = [[0,1,0],[0,-1,0],[1,0,0],[-1,0,0],[0,0,1],[0,0,-1]]
        for (const [dx, dy, dz] of offsets) {
            const next = pos.offset(dx, dy, dz)
            if (!visited.has(_posKey(next))) queue.push(next)
        }
    }

    return result.sort((a, b) => a.y - b.y)
}

// 疊方塊往上：看下 → 跳 → 馬上放方塊，連續動作不需要確認時機
async function _pillarUp(bot, targetY, targetPos) {
    while (Math.floor(bot.entity.position.y) < targetY - 1) {
        if (!isChopping) return
        const scaffold = bot.inventory.items().find(i => SCAFFOLD_BLOCKS.has(i.name))
        if (!scaffold) { console.log('[Wood] 沒有疊腳材料'); break }

        await bot.equip(scaffold, 'hand')
        await bot.look(bot.entity.yaw, Math.PI / 2, true)  // 看正下方

        const below = bot.blockAt(bot.entity.position.floored().offset(0, -1, 0))
        if (!below || below.name === 'air') break

        // 疊腳目標位置（bot 腳底的那格）必須是空氣才能放
        const placeTarget = bot.blockAt(below.position.offset(0, 1, 0))
        if (placeTarget && placeTarget.name !== 'air') {
            if (placeTarget.name.includes('leaves')) {
                try { await bot.dig(placeTarget) } catch (_) {}  // 樹葉先挖掉
            } else {
                break  // 木頭或其他實心方塊，已夠近，停止疊腳
            }
        }

        // 確認頭上有空間可以跳（bot 高 2 格，需要 +2 格是空的）
        const headBlock = bot.blockAt(bot.entity.position.floored().offset(0, 2, 0))
        if (headBlock && headBlock.name !== 'air') {
            if (headBlock.name.includes('leaves')) {
                try { await bot.dig(headBlock) } catch (_) {}
            } else {
                break  // 頭上有實心方塊，跳不起來
            }
        }

        const beforeY = bot.entity.position.y

        bot.setControlState('jump', true)
        try { await bot.placeBlock(below, new Vec3(0, 1, 0)) } catch (_) {}
        bot.setControlState('jump', false)

        await _sleep(400)

        // 放方塊失敗（位置沒變），不要繼續跳
        if (bot.entity.position.y <= beforeY + 0.1) break
        // 目標已在攻擊範圍內，不需再疊
        if (targetPos && bot.entity.position.distanceTo(targetPos) <= 4.5) break
    }
}

// 挖回所有疊腳方塊（SCAFFOLD_BLOCKS 都回收）
async function _reclaimScaffold(bot, groundY) {
    while (true) {
        if (Math.floor(bot.entity.position.y) <= groundY) break
        const below = bot.blockAt(bot.entity.position.floored().offset(0, -1, 0))
        if (!below || !SCAFFOLD_BLOCKS.has(below.name)) break
        try {
            await ensureToolFor(bot, below.name)
            await bot.dig(below)
            console.log(`[Wood] 回收 ${below.name}`)
            await _sleep(300)
        } catch (e) {
            break
        }
    }
    // 挖完疊腳方塊後換回斧頭
    const axe = bot.inventory.items().find(i => i.name.endsWith('_axe'))
    if (axe) await bot.equip(axe, 'hand')
}

// 撿起附近掉落的物品
async function _collectNearby(bot, nearPos, maxDistance) {
    const items = Object.values(bot.entities).filter(
        e => e.name === 'item' && e.position.distanceTo(nearPos) < maxDistance
    )
    for (const e of items) {
        if (!isChopping) return
        try {
            await bot.pathfinder.goto(
                new goals.GoalNear(e.position.x, e.position.y, e.position.z, 1)
            )
            await _sleep(150)
        } catch (_) {}
    }
}

// 在砍完的樹根位置種樹苗
async function _plantSapling(bot, rootPos, logName) {
    if (!logName) return
    const saplingName = LOG_TO_SAPLING[logName]
    if (!saplingName) return
    const sapling = bot.inventory.items().find(i => i.name === saplingName)
    if (!sapling) return

    // 樹根最底層往下一格應該是泥土/草地
    const groundBlock = bot.blockAt(rootPos.offset(0, -1, 0))
    if (!groundBlock || !VALID_SOIL.has(groundBlock.name)) return

    // 種植位置（rootPos）需要是空氣
    const plantSpot = bot.blockAt(rootPos)
    if (!plantSpot || plantSpot.name !== 'air') return

    try {
        await bot.pathfinder.goto(
            new goals.GoalNear(rootPos.x, groundBlock.position.y, rootPos.z, 2)
        )
        await bot.equip(sapling, 'hand')
        await bot.placeBlock(groundBlock, new Vec3(0, 1, 0))
        console.log(`[Wood] 種下 ${saplingName}`)
    } catch (e) {
        console.log(`[Wood] 種樹苗失敗: ${e.message}`)
    }
}

function _posKey(pos) {
    return `${Math.floor(pos.x)},${Math.floor(pos.y)},${Math.floor(pos.z)}`
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

function isActive() {
    return isChopping
}

module.exports = { startChopping, stopChopping, isActive }
