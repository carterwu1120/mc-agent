const { goals, Movements } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
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
            bot.pathfinder.setMovements(movements)

            const groundY = Math.floor(bot.entity.position.y)

            // 先水平走近（不疊方塊）
            try {
                await bot.pathfinder.goto(new goals.GoalNear(pos.x, bot.entity.position.y, pos.z, 3))
            } catch (e) { /* 走不到也繼續，試試疊方塊 */ }

            // 如果目標比現在高超過 2 格，手動疊方塊上去
            const heightDiff = pos.y - Math.floor(bot.entity.position.y)
            if (heightDiff > 2) {
                await _pillarUp(bot, pos.y, pos)
            }

            reachedAny = true  // 疊完就算到了，不再 pathfind（避免原地跳）

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
