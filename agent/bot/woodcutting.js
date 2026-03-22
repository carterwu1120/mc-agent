const { goals, Movements } = require('mineflayer-pathfinder')
const { setActivity } = require('./activity')
const { ensureAxe } = require('./crafting')

let isChopping = false

const SCAFFOLD_BLOCKS = new Set([
    'dirt', 'cobblestone', 'gravel', 'sand', 'stone',
    'oak_planks', 'spruce_planks', 'birch_planks', 'jungle_planks', 'acacia_planks',
])

async function startChopping(bot) {
    if (isChopping) {
        console.log('[Wood] 已在砍樹中')
        return
    }
    isChopping = true
    setActivity('chopping')
    console.log('[Wood] 開始砍樹')
    _loop(bot)
}

function stopChopping(bot) {
    if (!isChopping) return
    isChopping = false
    setActivity('idle')
    console.log('[Wood] 停止砍樹')
}

async function _loop(bot) {
    const skipped = new Set()  // 完全無法到達的樹根位置

    while (isChopping) {
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
        console.log(`[Wood] 找到樹，共 ${treeBlocks.length} 個木頭`)

        let reachedAny = false

        for (const pos of treeBlocks) {
            if (!isChopping) return

            const block = bot.blockAt(pos)
            if (!block || !block.name.endsWith('_log')) continue

            const movements = new Movements(bot)
            movements.canDig = true
            movements.scafoldingBlocks = _getScaffoldIds(bot)
            bot.pathfinder.setMovements(movements)

            const groundY = Math.floor(bot.entity.position.y)

            try {
                await bot.pathfinder.goto(new goals.GoalNear(pos.x, pos.y, pos.z, 3))
                reachedAny = true
            } catch (e) {
                console.log(`[Wood] 無法到達 (${pos.x},${pos.y},${pos.z})，跳過`)
                continue
            }

            if (!isChopping) return

            // 疊方塊爬上去後重新裝備斧頭
            const axe = bot.inventory.items().find(i => i.name.endsWith('_axe'))
            if (axe) await bot.equip(axe, 'hand')

            const fresh = bot.blockAt(pos)
            if (!fresh || !fresh.name.endsWith('_log')) continue

            try {
                await bot.dig(fresh)
                console.log(`[Wood] 砍下 ${fresh.name} at y=${pos.y}`)
            } catch (e) {
                console.log('[Wood] 砍樹失敗:', e.message)
            }

            await _sleep(400)

            // 如果有爬高，把有價值的疊腳方塊挖回來，再安全下來
            if (Math.floor(bot.entity.position.y) > groundY + 1) {
                await _reclaimScaffold(bot, groundY)
            }
            // 如果還在高處（非木板疊腳 / 未完全回收），用 pathfinder 安全下來
            if (Math.floor(bot.entity.position.y) > groundY + 1) {
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
        }

        // 整棵樹一個都到不了，才把 rootPos 標記為跳過
        if (!reachedAny) {
            skipped.add(_posKey(rootPos))
        }
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

function _getScaffoldIds(bot) {
    return bot.inventory.items()
        .filter(i => SCAFFOLD_BLOCKS.has(i.name))
        .map(i => bot.registry.itemsByName[i.name]?.id)
        .filter(id => id !== undefined)
}

// 挖回有價值的疊腳方塊（木板），dirt/cobble 等留著不管
async function _reclaimScaffold(bot, groundY) {
    const RECLAIM = /^[a-z_]+_planks$/
    while (true) {
        if (Math.floor(bot.entity.position.y) <= groundY + 1) break
        const below = bot.blockAt(bot.entity.position.floored().offset(0, -1, 0))
        if (!below || !RECLAIM.test(below.name)) break
        try {
            await bot.dig(below)
            console.log(`[Wood] 回收 ${below.name}`)
            await _sleep(300)
        } catch (e) {
            break
        }
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
