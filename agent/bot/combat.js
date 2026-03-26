const { goals } = require('mineflayer-pathfinder')
const { setActivity } = require('./activity')
const bridge = require('./bridge')

const HOSTILE_MOBS = new Set([
    'zombie', 'skeleton', 'creeper', 'spider', 'cave_spider', 'witch',
    'enderman', 'slime', 'magma_cube', 'blaze', 'wither_skeleton',
    'zombie_villager', 'husk', 'stray', 'drowned', 'phantom',
    'pillager', 'vindicator', 'evoker', 'vex', 'ravager',
    'endermite', 'silverfish', 'guardian', 'elder_guardian',
])

const WEAPON_PRIORITY = [
    'netherite_sword', 'diamond_sword', 'iron_sword', 'stone_sword',
    'golden_sword', 'wooden_sword',
    'netherite_axe', 'diamond_axe', 'iron_axe', 'stone_axe', 'wooden_axe',
    'trident',
]

const ARMOR_TIERS = { netherite: 5, diamond: 4, iron: 3, chainmail: 2, golden: 2, leather: 1 }
const ARMOR_SLOTS = [
    { slot: 5, dest: 'head',  suffix: '_helmet' },
    { slot: 6, dest: 'torso', suffix: '_chestplate' },
    { slot: 7, dest: 'legs',  suffix: '_leggings' },
    { slot: 8, dest: 'feet',  suffix: '_boots' },
]

let isCombating = false
let _wasFishing = false
let _wasChopping = false
let _wasMining = false
let _wasSmelting = false
let _savedMiningGoal = {}

function _armorTier(name) {
    for (const [mat, tier] of Object.entries(ARMOR_TIERS)) {
        if (name.startsWith(mat + '_')) return tier
    }
    return 0
}

function _isNearlyBroken(item) {
    if (!item || !item.maxDurability) return false
    return (item.maxDurability - item.durabilityUsed) < item.maxDurability * 0.1
}

async function equipArmor(bot) {
    for (const { slot, dest, suffix } of ARMOR_SLOTS) {
        const current = bot.inventory.slots[slot]
        const currentTier = (current && !_isNearlyBroken(current)) ? _armorTier(current.name) : 0
        const best = bot.inventory.items()
            .filter(i => i.name.endsWith(suffix))
            .sort((a, b) => _armorTier(b.name) - _armorTier(a.name))[0]
        if (best && _armorTier(best.name) > currentTier) {
            try {
                await bot.equip(best, dest)
                console.log(`[Combat] 裝備 ${best.name}`)
            } catch (e) {
                console.log(`[Combat] 裝備失敗 ${best.name}: ${e.message}`)
            }
        }
    }
}

const DIAMOND_ARMOR_COST = { '_helmet': 5, '_chestplate': 8, '_leggings': 7, '_boots': 4 }

function _diamondReserve(bot) {
    // 預留給工具的鑽石數量（沒有就預留合成所需）
    const inv = bot.inventory.items()
    const reserve =
        (inv.some(i => i.name === 'diamond_pickaxe') ? 0 : 3) +
        (inv.some(i => i.name === 'diamond_sword')   ? 0 : 2)
    return reserve
}

async function craftMissingArmor(bot) {
    // 任何欄位低於鐵裝或快壞就觸發
    const needsUpgrade = ARMOR_SLOTS.some(({ slot }) => {
        const cur = bot.inventory.slots[slot]
        return !cur || _isNearlyBroken(cur) || _armorTier(cur.name) < ARMOR_TIERS.iron
    })
    if (!needsUpgrade) return

    const { ensureCraftingTable, reclaimCraftingTable } = require('./crafting')

    // 計算缺少的鐵裝需要多少 iron_ingot，不足就先燒製
    const ironIngotCount = () => bot.inventory.items()
        .filter(i => i.name === 'iron_ingot').reduce((s, i) => s + i.count, 0)
    const ingotNeeded = ARMOR_SLOTS.reduce((sum, { slot, suffix }) => {
        const cur = bot.inventory.slots[slot]
        const tier = (cur && !_isNearlyBroken(cur)) ? _armorTier(cur.name) : 0
        if (tier >= ARMOR_TIERS.iron) return sum
        const diamonds = bot.inventory.items()
            .filter(i => i.name === 'diamond').reduce((s, i) => s + i.count, 0)
        const canDiamond = diamonds - _diamondReserve(bot) >= DIAMOND_ARMOR_COST[suffix]
        return canDiamond ? sum : sum + DIAMOND_ARMOR_COST[suffix]
    }, 0)
    const stillNeed = ingotNeeded - ironIngotCount()
    if (stillNeed > 0) {
        const rawIron = bot.inventory.items().filter(i => i.name === 'raw_iron').reduce((s, i) => s + i.count, 0)
        if (rawIron > 0) {
            const toSmelt = Math.min(rawIron, stillNeed)
            const ingotsBefore = ironIngotCount()
            console.log(`[Combat] 先燒製 ${toSmelt} 個 iron_ingot`)
            const { startSmelting, isActive: isSmeltingActive } = require('./smelting')
            startSmelting(bot, { target: 'iron', count: toSmelt })
            while (isSmeltingActive()) {
                await _sleep(3000)
            }
            if (ironIngotCount() < ingotsBefore + toSmelt) {
                console.log(`[Combat] 燒製提前結束，只取得 ${ironIngotCount() - ingotsBefore}/${toSmelt} 個 iron_ingot`)
            }
        }
        if (rawIron === 0) {
            console.log(`[Combat] 缺少鐵材料，通知 Python 決策`)
            bridge.sendState(bot, 'craft_decision', {
                goal: 'iron_armor',
                options: [],
                reason: 'material_missing',
                missing_materials: [{ name: 'iron_ingot', count: stillNeed }],
            })
            return
        }
    }

    const table = await ensureCraftingTable(bot)
    if (!table) return

    try {
        for (const { slot, dest, suffix } of ARMOR_SLOTS) {
            const cur = bot.inventory.slots[slot]
            const currentTier = (cur && !_isNearlyBroken(cur)) ? _armorTier(cur.name) : 0
            if (currentTier >= ARMOR_TIERS.iron) continue

            // 決定嘗試的材料：鑽石夠用才試，否則直接鐵
            const diamonds = bot.inventory.items()
                .filter(i => i.name === 'diamond').reduce((s, i) => s + i.count, 0)
            const reserve = _diamondReserve(bot)
            const canUseDiamond = diamonds - reserve >= DIAMOND_ARMOR_COST[suffix]

            const tiers = canUseDiamond ? ['diamond', 'iron'] : ['iron']

            for (const mat of tiers) {
                const pieceName = `${mat}${suffix}`
                const pieceItem = bot.registry.itemsByName[pieceName]
                if (!pieceItem) continue
                const recipe = bot.recipesFor(pieceItem.id, null, 1, table)[0]
                if (!recipe) continue
                try {
                    await bot.craft(recipe, 1, table)
                    console.log(`[Combat] 合成 ${pieceName}`)
                    const crafted = bot.inventory.items().find(i => i.name === pieceName)
                    if (crafted) await bot.equip(crafted, dest)
                    break
                } catch (e) {
                    console.log(`[Combat] 合成 ${pieceName} 失敗: ${e.message}`)
                }
            }
        }
    } finally {
        await reclaimCraftingTable(bot)
    }
}

async function equipWeapon(bot) {
    const SWORDS = WEAPON_PRIORITY.filter(n => n.endsWith('_sword'))
    const AXES   = WEAPON_PRIORITY.filter(n => n.endsWith('_axe') || n === 'trident')

    const _tryEquip = async (name) => {
        const item = bot.inventory.items().find(i => i.name === name)
        if (!item) return false
        try { await bot.equip(item, 'hand'); console.log(`[Combat] 武器 ${item.name}`); return true } catch (_) { return false }
    }

    // 1. 找劍
    for (const name of SWORDS) { if (await _tryEquip(name)) return true }

    // 2. 沒劍 → 嘗試合成
    console.log('[Combat] 背包無劍，嘗試合成...')
    const { ensureSword } = require('./crafting')
    const crafted = await ensureSword(bot)
    if (crafted) {
        for (const name of SWORDS) { if (await _tryEquip(name)) return true }
    }

    // 3. 合成失敗 → 找斧頭
    for (const name of AXES) { if (await _tryEquip(name)) return true }

    // 4. 什麼都沒有 → 空手
    try { await bot.unequip('hand') } catch (_) {}
    return false
}

async function startCombat(bot, goal = {}) {
    if (isCombating) return

    const { isActive: isFishing, stopFishing, startFishing } = require('./fishing')
    const { isActive: isChopping, stopChopping, startChopping } = require('./woodcutting')
    const { isActive: isMining, stopMining, startMining, getGoal: getMiningGoal } = require('./mining')
    const { isActive: isSmelting, stopSmelting, startSmelting } = require('./smelting')

    _wasFishing = isFishing()
    _wasChopping = isChopping()
    _wasMining = isMining()
    _wasSmelting = isSmelting()
    if (_wasFishing) stopFishing(bot)
    if (_wasChopping) stopChopping(bot)
    if (_wasMining) { _savedMiningGoal = getMiningGoal(); stopMining(bot) }
    if (_wasSmelting) stopSmelting(bot)

    isCombating = true
    setActivity('combat')
    console.log(`[Combat] 開始戰鬥 goal=${JSON.stringify(goal)}`)
    _loop(bot, goal)
}

function stopCombat(bot) {
    if (!isCombating) return
    isCombating = false
    setActivity('idle')
    console.log('[Combat] 停止戰鬥')

    const { startFishing } = require('./fishing')
    const { startChopping } = require('./woodcutting')
    const { startMining } = require('./mining')
    const { startSmelting } = require('./smelting')

    if (_wasFishing) { console.log('[Combat] 恢復釣魚'); startFishing(bot) }
    if (_wasChopping) { console.log('[Combat] 恢復砍樹'); startChopping(bot) }
    if (_wasMining) { console.log('[Combat] 恢復挖礦'); startMining(bot, _savedMiningGoal) }
    if (_wasSmelting) { console.log('[Combat] 恢復燒製'); startSmelting(bot) }

    _wasFishing = false
    _wasChopping = false
    _wasMining = false
    _wasSmelting = false
    _savedMiningGoal = {}

    // 戰鬥結束後升級裝備（可能撿到掉落 / 背包有材料）
    setTimeout(async () => {
        await craftMissingArmor(bot)
        await equipArmor(bot)
    }, 1000)
}

function isActive() {
    return isCombating
}

async function _loop(bot, goal = {}) {
    const startTime = Date.now()
    let noTargetTicks = 0

    while (isCombating) {
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Combat] 達到時間目標 ${goal.duration}s，停止`)
            break
        }

        const target = _findTarget(bot, goal.target)
        if (!target || !target.isValid) {
            noTargetTicks++
            if (noTargetTicks >= 4) {
                console.log('[Combat] 附近無敵對生物，結束戰鬥')
                break
            }
            await _sleep(500)
            continue
        }
        noTargetTicks = 0

        // 每次攻擊前確認手持武器（其他模組可能切換了手上物品）
        const handItem = bot.inventory.slots[bot.getEquipmentDestSlot('hand')]
        if (!handItem || !WEAPON_PRIORITY.includes(handItem.name)) {
            await equipWeapon(bot)
        }

        const dist = target.position.distanceTo(bot.entity.position)
        if (dist > 3) {
            try {
                await bot.pathfinder.goto(
                    new goals.GoalNear(target.position.x, target.position.y, target.position.z, 2)
                )
            } catch (_) {}
        }

        if (!isCombating) break

        try {
            await bot.lookAt(target.position.offset(0, (target.height ?? 1.8) / 2, 0))
            bot.attack(target)
        } catch (e) {
            console.log(`[Combat] 攻擊失敗: ${e.message}`)
        }

        await _sleep(600)
    }

    if (isCombating) stopCombat(bot)
}

function _findTarget(bot, preferType) {
    const selfPos = bot.entity.position
    return Object.values(bot.entities)
        .filter(e => {
            if (e.id === bot.entity.id) return false
            if (!e.isValid || !e.position) return false
            if (e.position.distanceTo(selfPos) > 16) return false
            const name = e.name || e.mobType || ''
            if (preferType) return name.toLowerCase().includes(preferType.toLowerCase())
            return HOSTILE_MOBS.has(name.toLowerCase())
        })
        .sort((a, b) => a.position.distanceTo(selfPos) - b.position.distanceTo(selfPos))[0]
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

function startMonitor(bot) {
    let _lastHealth = null

    bot.on('health', () => {
        if (_lastHealth === null) { _lastHealth = bot.health; return }
        if (bot.health < _lastHealth - 0.5 && !isCombating) {
            const target = _findTarget(bot)
            if (target) {
                console.log(`[Combat] 受到攻擊！血量 ${_lastHealth} → ${bot.health}，反擊 ${target.name}`)
                startCombat(bot)
            } else {
                console.log(`[Combat] 血量下降 ${_lastHealth} → ${bot.health}，附近無敵對生物（環境傷害），忽略`)
            }
        }
        _lastHealth = bot.health
    })

    bot.on('playerCollect', (collector) => {
        if (collector.username !== bot.username) return
        setTimeout(() => equipArmor(bot), 500)
    })

    console.log('[Combat] 戰鬥監控已啟動')
}

module.exports = { startCombat, stopCombat, isActive, equipArmor, equipWeapon, craftMissingArmor, startMonitor }
