const { goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const { setActivity } = require('./activity')
const bridge = require('./bridge')

let isSmelting = false
let _placedFurnacePos = null

const SMELTABLE = {
    raw_iron: 'iron_ingot',         iron_ore: 'iron_ingot',         deepslate_iron_ore: 'iron_ingot',
    raw_gold: 'gold_ingot',         gold_ore: 'gold_ingot',         deepslate_gold_ore: 'gold_ingot',
    raw_copper: 'copper_ingot',     copper_ore: 'copper_ingot',     deepslate_copper_ore: 'copper_ingot',
    sand: 'glass',                  cobblestone: 'stone',
}

const FUEL_PRIORITY = [
    'coal', 'charcoal',
    'oak_log', 'spruce_log', 'birch_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'mangrove_log',
    'oak_planks', 'spruce_planks', 'birch_planks', 'jungle_planks', 'acacia_planks', 'dark_oak_planks', 'dark_oak_planks',
]

async function startSmelting(bot, goal = {}) {
    if (isSmelting) {
        console.log('[Smelt] 已在燒製中')
        return
    }
    isSmelting = true
    setActivity('smelting')
    console.log('[Smelt] 開始燒製')
    _loop(bot, goal)
}

function stopSmelting(bot) {
    if (!isSmelting) return
    isSmelting = false
    setActivity('idle')
    console.log('[Smelt] 停止燒製')
}

function isActive() {
    return isSmelting
}

async function _loop(bot, goal = {}) {
    const startTime = Date.now()
    let smeltedCount = 0

    while (isSmelting) {
        // 停止條件
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Smelt] 達到時間目標 ${goal.duration}s，停止`)
            isSmelting = false
            setActivity('idle')
            bridge.sendState(bot, 'activity_done', { activity: 'smelting', reason: 'goal_reached' })
            break
        }
        if (goal.target && goal.count && smeltedCount >= goal.count) {
            console.log(`[Smelt] 達到目標 ${goal.target} x${goal.count}，停止`)
            isSmelting = false
            setActivity('idle')
            bridge.sendState(bot, 'activity_done', { activity: 'smelting', reason: 'goal_reached' })
            break
        }

        // 找或放置熔爐
        const furnaceBlock = await _findOrPlaceFurnace(bot)
        if (!furnaceBlock) {
            console.log('[Smelt] 找不到熔爐，停止')
            isSmelting = false
            setActivity('idle')
            break
        }

        // 走過去
        try {
            const p = furnaceBlock.position
            await bot.pathfinder.goto(new goals.GoalNear(p.x, p.y, p.z, 2))
        } catch (e) {
            console.log('[Smelt] 無法走到熔爐:', e.message)
            await _sleep(2000)
            continue
        }

        if (!isSmelting) return

        // 開啟熔爐
        let furnace
        try {
            furnace = await bot.openFurnace(furnaceBlock)
        } catch (e) {
            console.log('[Smelt] 開啟熔爐失敗:', e.message)
            await _sleep(2000)
            continue
        }

        try {
            // 取出已完成產物
            if (furnace.output) {
                const out = furnace.output
                await furnace.takeOutput()
                smeltedCount += out.count
                console.log(`[Smelt] 取出 ${out.name} x${out.count}（共 ${smeltedCount}）`)
            }

            // 找背包裡可燒的材料
            const smeltableItems = bot.inventory.items().filter(i => {
                if (!SMELTABLE[i.name]) return false
                if (goal.target) return i.name.includes(goal.target)
                return true
            })

            // 沒材料且 input slot 也空 → 停止
            if (smeltableItems.length === 0 && !furnace.input) {
                console.log('[Smelt] 背包沒有可燒的材料，停止')
                furnace.close()
                isSmelting = false
                setActivity('idle')
                bridge.sendState(bot, 'activity_done', { activity: 'smelting', reason: 'no_input' })
                break
            }

            // 加燃料（fuel slot 空或剩餘量不足時補充）
            if (!furnace.fuel) {
                const fuelItem = bot.inventory.items().find(i => FUEL_PRIORITY.includes(i.name))
                if (fuelItem) {
                    const fuelCount = Math.min(fuelItem.count, 64)
                    await furnace.putFuel(fuelItem.type, null, fuelCount)
                    console.log(`[Smelt] 放入燃料 ${fuelItem.name} x${fuelCount}`)
                } else {
                    console.log('[Smelt] 沒有燃料，等待中...')
                }
            }

            // 放入材料（input slot 空時才放）
            if (!furnace.input && smeltableItems.length > 0) {
                const inputItem = smeltableItems[0]
                let count = inputItem.count
                if (goal.count) count = Math.min(count, goal.count - smeltedCount)
                count = Math.min(count, 64)
                if (count > 0) {
                    await furnace.putInput(inputItem.type, null, count)
                    console.log(`[Smelt] 放入 ${inputItem.name} x${count}`)
                }
            }

            furnace.close()
        } catch (e) {
            console.log('[Smelt] 燒製操作失敗:', e.message)
            try { furnace.close() } catch (_) {}
        }

        // 等待燒製進度（每 5 秒檢查一次）
        console.log('[Smelt] 等待燒製...')
        await _sleep(5000)
    }
}

async function _findOrPlaceFurnace(bot) {
    const furnaceId    = bot.registry.blocksByName['furnace']?.id
    const litFurnaceId = bot.registry.blocksByName['lit_furnace']?.id
    if (!furnaceId) return null

    // 找現有熔爐
    const existing = bot.findBlock({
        matching: b => b.type === furnaceId || (litFurnaceId && b.type === litFurnaceId),
        maxDistance: 32,
    })
    if (existing) return existing

    // 背包沒有熔爐 → 嘗試合成
    if (!bot.inventory.items().some(i => i.name === 'furnace')) {
        const cobble = bot.inventory.items()
            .filter(i => i.name === 'cobblestone')
            .reduce((s, i) => s + i.count, 0)
        if (cobble < 8) {
            console.log('[Smelt] 沒有熔爐且圓石不足 8 個，無法合成')
            return null
        }
        const crafted = await _craftFurnace(bot)
        if (!crafted) return null
    }

    return await _placeFurnace(bot, furnaceId)
}

async function _craftFurnace(bot) {
    const { ensureCraftingTable } = require('./crafting')
    const table = await ensureCraftingTable(bot)
    if (!table) return false

    const item = bot.registry.itemsByName['furnace']
    if (!item) return false
    const recipe = bot.recipesFor(item.id, null, 1, table)[0]
    if (!recipe) {
        console.log('[Smelt] 找不到熔爐合成配方')
        return false
    }
    try {
        await bot.craft(recipe, 1, table)
        console.log('[Smelt] 合成熔爐')
        return true
    } catch (e) {
        console.log('[Smelt] 合成熔爐失敗:', e.message)
        return false
    }
}

async function _placeFurnace(bot, furnaceId) {
    const item = bot.inventory.items().find(i => i.name === 'furnace')
    if (!item) return null
    await bot.equip(item, 'hand')

    const pos = bot.entity.position.floored()
    const dirs = [[1,0],[0,1],[-1,0],[0,-1]]

    // 收集候選放置位置，優先選已有空氣的
    const candidates = []
    for (const [dx, dz] of dirs) {
        let ground = null
        for (let dy = -1; dy >= -3; dy--) {
            const b = bot.blockAt(pos.offset(dx, dy, dz))
            if (b && b.boundingBox === 'block') { ground = b; break }
        }
        if (!ground) continue

        const spacePos = ground.position.offset(0, 1, 0)
        if (spacePos.distanceTo(bot.entity.position) > 4) continue

        const space = bot.blockAt(spacePos)
        const isOpen = space && (space.name === 'air' || space.name === 'cave_air')
        candidates.push({ ground, spacePos, needDig: !isOpen, space })
    }

    candidates.sort((a, b) => a.needDig - b.needDig)

    for (const { ground, spacePos, needDig, space } of candidates) {
        if (needDig) {
            if (!space || space.boundingBox !== 'block') continue
            try { await bot.dig(space) } catch (_) { continue }
            const fresh = bot.blockAt(spacePos)
            if (!fresh || (fresh.name !== 'air' && fresh.name !== 'cave_air')) continue
        }

        try {
            await bot.equip(item, 'hand')
            await bot.lookAt(ground.position.offset(0.5, 1, 0.5))
            await bot.placeBlock(ground, new Vec3(0, 1, 0))
            await _sleep(400)
            const placed = bot.findBlock({ matching: furnaceId, maxDistance: 4 })
            if (placed) {
                console.log('[Smelt] 放置熔爐')
                _placedFurnacePos = placed.position.clone()
                return placed
            }
        } catch (e) {
            console.log('[Smelt] 放置熔爐失敗:', e.message)
        }
    }

    console.log('[Smelt] 找不到放置位置')
    return null
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

module.exports = { startSmelting, stopSmelting, isActive }
