const WEAPON_PRIORITY = [
    'diamond_sword', 'iron_sword', 'stone_sword', 'golden_sword', 'wooden_sword',
    'diamond_axe', 'iron_axe', 'stone_axe', 'golden_axe', 'wooden_axe',
]

const ARMOR_PRIORITY = {
    head: ['diamond_helmet', 'iron_helmet', 'chainmail_helmet', 'golden_helmet', 'leather_helmet'],
    torso: ['diamond_chestplate', 'iron_chestplate', 'chainmail_chestplate', 'golden_chestplate', 'leather_chestplate'],
    legs: ['diamond_leggings', 'iron_leggings', 'chainmail_leggings', 'golden_leggings', 'leather_leggings'],
    feet: ['diamond_boots', 'iron_boots', 'chainmail_boots', 'golden_boots', 'leather_boots'],
}
const ARMOR_COSTS = {
    diamond_helmet: 5,
    diamond_chestplate: 8,
    diamond_leggings: 7,
    diamond_boots: 4,
    iron_helmet: 5,
    iron_chestplate: 8,
    iron_leggings: 7,
    iron_boots: 4,
}

const EQUIP_SLOTS = ['hand', 'off-hand', 'head', 'torso', 'legs', 'feet']
const SLOT_ALIASES = {
    offhand: 'off-hand',
    off_hand: 'off-hand',
    shield: 'off-hand',
    chest: 'torso',
    chestplate: 'torso',
    helmet: 'head',
    leggings: 'legs',
    boots: 'feet',
}

function _isUsable(item) {
    if (!item) return false
    if (!item.maxDurability) return true  // no durability (e.g. netherite has it, but non-tool items don't)
    return item.durabilityUsed < item.maxDurability
}

function _durabilityPct(item) {
    if (!item || !item.maxDurability) return 100
    return Math.max(0, Math.round(((item.maxDurability - item.durabilityUsed) / item.maxDurability) * 100))
}

function _findBestItem(bot, names) {
    // Collect all usable candidates (in priority order = material tier)
    const candidates = names
        .map(name => bot.inventory.items().find(i => i.name === name && _isUsable(i)))
        .filter(Boolean)
    if (candidates.length === 0) return null

    // If the best-tier item has > 10% durability, prefer it (material wins)
    // Otherwise, pick the candidate with the highest durability pct (durability wins)
    const best = candidates[0]
    if (_durabilityPct(best) > 10) return best
    return candidates.reduce((a, b) => _durabilityPct(a) >= _durabilityPct(b) ? a : b)
}

function _normalizeSlot(target) {
    if (!target) return null
    return SLOT_ALIASES[target] ?? target
}

function _countItem(bot, itemName) {
    return bot.inventory.items()
        .filter(i => i.name === itemName)
        .reduce((sum, i) => sum + i.count, 0)
}

function _diamondReserve(bot) {
    const inv = bot.inventory.items()
    return (
        (inv.some(i => i.name === 'diamond_pickaxe') ? 0 : 3) +
        (inv.some(i => i.name === 'diamond_sword') ? 0 : 2)
    )
}

function _canCraftArmorItem(bot, itemName) {
    const cost = ARMOR_COSTS[itemName] ?? 0
    if (itemName.startsWith('diamond_')) {
        return _countItem(bot, 'diamond') - _diamondReserve(bot) >= cost
    }
    if (itemName.startsWith('iron_')) {
        return _countItem(bot, 'iron_ingot') >= cost
    }
    return false
}

async function _craftArmorUpgrade(bot, slot) {
    const priority = ARMOR_PRIORITY[slot]
    if (!priority) return null

    const { ensureCraftingTable, reclaimCraftingTable } = require('./crafting')
    const table = await ensureCraftingTable(bot)
    if (!table) return null

    try {
        for (const itemName of priority) {
            const itemDef = bot.registry.itemsByName[itemName]
            if (!itemDef) continue

            if (!_canCraftArmorItem(bot, itemName) && (itemName.startsWith('diamond_') || itemName.startsWith('iron_'))) continue

            const recipe = bot.recipesFor(itemDef.id, null, 1, table)[0]
            if (!recipe) continue

            try {
                await bot.craft(recipe, 1, table)
                const crafted = bot.inventory.items().find(i => i.name === itemName)
                if (crafted) {
                    console.log(`[Equip] 合成 ${itemName}`)
                    return crafted
                }
            } catch (e) {
                console.log(`[Equip] 合成 ${itemName} 失敗: ${e.message}`)
            }
        }
    } finally {
        await reclaimCraftingTable(bot)
    }

    return null
}

async function equipBestWeapon(bot) {
    const weapon = _findBestItem(bot, WEAPON_PRIORITY)
    if (!weapon) {
        console.log('[Equip] 背包裡沒有可用武器')
        return null
    }

    try {
        await bot.equip(weapon, 'hand')
        console.log(`[Equip] 裝備武器 ${weapon.name}`)
        return weapon.name
    } catch (e) {
        console.log(`[Equip] 裝備武器失敗: ${e.message}`)
        return null
    }
}

async function equipShield(bot) {
    const shield = bot.inventory.items().find(i => i.name === 'shield')
    if (!shield) {
        console.log('[Equip] 背包裡沒有盾牌')
        return null
    }

    try {
        await bot.equip(shield, 'off-hand')
        console.log('[Equip] 裝備盾牌到 off-hand')
        return shield.name
    } catch (e) {
        console.log(`[Equip] 裝備盾牌失敗: ${e.message}`)
        return null
    }
}

async function equipBestArmor(bot) {
    const equipped = []

    for (const [slot, priority] of Object.entries(ARMOR_PRIORITY)) {
        let armor = _findBestItem(bot, priority)
        const topTierName = priority[0]
        if ((!armor || armor.name !== topTierName) && _canCraftArmorItem(bot, topTierName)) {
            armor = await _craftArmorUpgrade(bot, slot)
        } else if (!armor) {
            armor = await _craftArmorUpgrade(bot, slot)
        }
        if (!armor) continue

        try {
            await bot.equip(armor, slot)
            equipped.push(armor.name)
            console.log(`[Equip] 穿上 ${armor.name}`)
        } catch (e) {
            console.log(`[Equip] 穿裝 ${armor.name} 失敗: ${e.message}`)
        }
    }

    if (equipped.length === 0) {
        console.log('[Equip] 背包裡沒有可用護甲')
    }
    return equipped
}

async function equipBestLoadout(bot) {
    const armor = await equipBestArmor(bot)
    const weapon = await equipBestWeapon(bot)
    return { armor, weapon }
}

function _slotForItemName(itemName) {
    if (!itemName) return null
    if (itemName === 'shield') return 'off-hand'
    if (itemName.endsWith('_helmet')) return 'head'
    if (itemName.endsWith('_chestplate')) return 'torso'
    if (itemName.endsWith('_leggings')) return 'legs'
    if (itemName.endsWith('_boots')) return 'feet'
    if (itemName.endsWith('_sword') || itemName.endsWith('_axe') || itemName.endsWith('_pickaxe') || itemName.endsWith('_shovel')) return 'hand'
    return null
}

function _getEquippedItem(bot, slot) {
    if (slot === 'hand') return bot.heldItem ?? null
    if (!EQUIP_SLOTS.includes(slot)) return null
    return bot.inventory.slots[bot.getEquipmentDestSlot(slot)] ?? null
}

async function equipSpecific(bot, target) {
    if (!target) return null
    target = _normalizeSlot(target)

    if (target === 'hand') {
        return await equipBestWeapon(bot)
    }
    if (target === 'off-hand') {
        return await equipShield(bot)
    }
    if (Object.prototype.hasOwnProperty.call(ARMOR_PRIORITY, target)) {
        let armor = _findBestItem(bot, ARMOR_PRIORITY[target])
        const topTierName = ARMOR_PRIORITY[target][0]
        if ((!armor || armor.name !== topTierName) && _canCraftArmorItem(bot, topTierName)) {
            armor = await _craftArmorUpgrade(bot, target)
        } else if (!armor) {
            armor = await _craftArmorUpgrade(bot, target)
        }
        if (!armor) {
            console.log(`[Equip] 背包裡沒有可裝到 ${target} 的護甲`)
            return null
        }
        try {
            await bot.equip(armor, target)
            console.log(`[Equip] 穿上 ${armor.name}`)
            return armor.name
        } catch (e) {
            console.log(`[Equip] 穿裝 ${armor.name} 失敗: ${e.message}`)
            return null
        }
    }

    const item = bot.inventory.items().find(i => i.name === target)
    if (!item) {
        console.log(`[Equip] 背包裡沒有 ${target}`)
        return null
    }

    const slot = _slotForItemName(item.name)
    if (!slot) {
        console.log(`[Equip] 不知道 ${item.name} 應該裝備到哪個欄位`)
        return null
    }

    try {
        await bot.equip(item, slot)
        console.log(`[Equip] 裝備 ${item.name} 到 ${slot}`)
        return item.name
    } catch (e) {
        console.log(`[Equip] 裝備 ${item.name} 失敗: ${e.message}`)
        return null
    }
}

async function unequipAll(bot) {
    const removed = []

    for (const slot of EQUIP_SLOTS) {
        try {
            const current = _getEquippedItem(bot, slot)
            if (!current) continue

            await bot.unequip(slot)
            removed.push(current.name)
            console.log(`[Equip] 卸下 ${slot}: ${current.name}`)
        } catch (e) {
            console.log(`[Equip] 卸下 ${slot} 失敗: ${e.message}`)
        }
    }

    if (removed.length === 0) {
        console.log('[Equip] 目前沒有已裝備的物品')
    }
    return removed
}

async function unequipSpecific(bot, target) {
    if (!target) return []
    target = _normalizeSlot(target)

    const slot = EQUIP_SLOTS.includes(target)
        ? target
        : _slotForItemName(target)

    if (!slot) {
        console.log(`[Equip] 不知道要從哪個欄位卸下 ${target}`)
        return []
    }

    const current = _getEquippedItem(bot, slot)
    if (!current) {
        console.log(`[Equip] ${slot} 目前沒有裝備`)
        return []
    }

    if (!EQUIP_SLOTS.includes(target) && current.name !== target) {
        console.log(`[Equip] ${target} 目前沒有裝備在 ${slot}（現在是 ${current.name}）`)
        return []
    }

    try {
        await bot.unequip(slot)
        console.log(`[Equip] 卸下 ${slot}: ${current.name}`)
        return [current.name]
    } catch (e) {
        console.log(`[Equip] 卸下 ${slot} 失敗: ${e.message}`)
        return []
    }
}

module.exports = { equipBestArmor, equipBestWeapon, equipBestLoadout, equipShield, equipSpecific, unequipAll, unequipSpecific }
