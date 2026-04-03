const { goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')

let _craftDecision = null
let _placedTablePos = null
let _lastTeleportLikeAt = 0

const REPLACEABLE_BLOCKS = new Set([
    'air', 'cave_air', 'short_grass', 'grass', 'tall_grass', 'fern', 'large_fern',
    'dead_bush', 'snow', 'vine', 'torch', 'wall_torch',
])

function applyCraftDecision(decision) {
    _craftDecision = decision
}

function _waitForCraftDecision(timeoutMs = 20000) {
    return new Promise((resolve) => {
        const check = setInterval(() => {
            if (_craftDecision !== null) {
                clearInterval(check)
                clearTimeout(timer)
                const d = _craftDecision
                _craftDecision = null
                resolve(d)
            }
        }, 200)
        const timer = setTimeout(() => {
            clearInterval(check)
            resolve(null)
        }, timeoutMs)
    })
}

// 通用合成決策：如果有多種可選，問 LLM；只有一種就直接用
async function chooseCraft(bot, goal, options) {
    if (options.length === 1) return options[0]
    console.log(`[Craft] 多種選項，詢問 LLM：${options.join(', ')}`)
    const bridge = require('./bridge')
    bridge.sendState(bot, 'craft_decision', { goal, options })
    const decision = await _waitForCraftDecision(20000)
    if (decision?.item && options.includes(decision.item)) {
        console.log(`[Craft] LLM 選擇：${decision.item}`)
        return decision.item
    }
    console.log(`[Craft] LLM 超時或無效，使用預設：${options[0]}`)
    return options[0]
}

const LOG_TO_PLANK = {
    oak_log:      'oak_planks',
    spruce_log:   'spruce_planks',
    birch_log:    'birch_planks',
    jungle_log:   'jungle_planks',
    acacia_log:   'acacia_planks',
    dark_oak_log: 'dark_oak_planks',
    mangrove_log: 'mangrove_planks',
}

const COMPACTABLE_ITEMS = {
    redstone: 'redstone_block',
    coal: 'coal_block',
    lapis_lazuli: 'lapis_block',
    iron_ingot: 'iron_block',
    gold_ingot: 'gold_block',
    diamond: 'diamond_block',
    emerald: 'emerald_block',
    copper_ingot: 'copper_block',
}

const TOOL_PRIORITY = {
    _axe:     ['diamond_axe',     'golden_axe',     'iron_axe',     'stone_axe',     'wooden_axe'],
    _pickaxe: ['diamond_pickaxe', 'iron_pickaxe',   'stone_pickaxe', 'wooden_pickaxe'],
    _shovel:  ['diamond_shovel',  'iron_shovel',    'stone_shovel',  'wooden_shovel'],
    _sword:   ['diamond_sword',   'iron_sword',     'stone_sword',   'golden_sword',  'wooden_sword'],
}

// 通用工具合成：確保背包有指定類型且達到最低等級的工具
// minTier: e.g. 'iron_pickaxe'（需要鐵稿以上），null 代表任何等級都可以
async function _ensureTool(bot, toolSuffix, minTier = null, autoChoose = false) {
    const priority = TOOL_PRIORITY[toolSuffix] ?? []
    const minIdx = minTier ? priority.indexOf(minTier) : priority.length - 1
    // acceptable = priority 裡等級 >= minTier 的選項（index 越小等級越高）
    const acceptable = minIdx === -1 ? priority : priority.slice(0, minIdx + 1)

    const existing = acceptable.map(name => bot.inventory.items().find(i => i.name === name)).find(Boolean)
    if (existing) {
        await bot.equip(existing, 'hand')
        return true
    }

    const toolName = minTier ?? toolSuffix.slice(1)
    console.log(`[Craft] 需要 ${toolName}，開始合成...`)

    await convertLogsToPlanks(bot, 3)
    const table = await ensureCraftingTable(bot)

    const stickCount = bot.inventory.items()
        .filter(i => i.name === 'stick')
        .reduce((s, i) => s + i.count, 0)
    if (stickCount < 2) await _craft(bot, 'stick', null)

    const craftable = acceptable.filter(name => {
        const item = bot.registry.itemsByName[name]
        if (!item || !table) return false
        return bot.recipesFor(item.id, null, 1, table).length > 0
    })

    const shouldTrySmeltUpgrade =
        acceptable.some(name => name.startsWith('iron_')) &&
        !craftable.some(name => name.startsWith('iron_')) &&
        craftable.some(name => name.startsWith('wooden_') || name.startsWith('stone_') || name.startsWith('golden_'))

    if (shouldTrySmeltUpgrade) {
        const smelted = await _smeltIfNeeded(bot, toolSuffix)
        if (smelted) {
            const table2 = await ensureCraftingTable(bot)
            const craftable2 = acceptable.filter(name => {
                const item = bot.registry.itemsByName[name]
                if (!item || !table2) return false
                return bot.recipesFor(item.id, null, 1, table2).length > 0
            })
            if (craftable2.some(name => name.startsWith('iron_'))) {
                const chosen2 = autoChoose ? craftable2[0] : await chooseCraft(bot, toolSuffix.slice(1), craftable2)
                const ok2 = await _craft(bot, chosen2, table2)
                await _reclaimCraftingTable(bot)
                if (ok2) {
                    const tool = bot.inventory.items().find(i => acceptable.includes(i.name))
                    if (tool) await bot.equip(tool, 'hand')
                }
                return ok2
            }
        }
    }

    if (craftable.length === 0) {
        // 先嘗試解壓縮方塊（e.g. diamond_block → 9 diamonds）
        const decompressed = await _decompressIfNeeded(bot, acceptable)
        if (decompressed) {
            const table2 = await ensureCraftingTable(bot)
            const craftable2 = acceptable.filter(name => {
                const item = bot.registry.itemsByName[name]
                if (!item || !table2) return false
                return bot.recipesFor(item.id, null, 1, table2).length > 0
            })
            if (craftable2.length > 0) {
                const chosen2 = autoChoose ? craftable2[0] : await chooseCraft(bot, toolSuffix.slice(1), craftable2)
                const ok2 = await _craft(bot, chosen2, table2)
                await _reclaimCraftingTable(bot)
                if (ok2) {
                    const tool = bot.inventory.items().find(i => acceptable.includes(i.name))
                    if (tool) await bot.equip(tool, 'hand')
                }
                return ok2
            }
        }

        // 嘗試燒製原礦後重試
        const smelted = await _smeltIfNeeded(bot, toolSuffix)
        if (smelted) {
            const table2 = await ensureCraftingTable(bot)
            const craftable2 = acceptable.filter(name => {
                const item = bot.registry.itemsByName[name]
                if (!item || !table2) return false
                return bot.recipesFor(item.id, null, 1, table2).length > 0
            })
            if (craftable2.length > 0) {
                const chosen2 = autoChoose ? craftable2[0] : await chooseCraft(bot, toolSuffix.slice(1), craftable2)
                const ok2 = await _craft(bot, chosen2, table2)
                await _reclaimCraftingTable(bot)
                if (ok2) {
                    const tool = bot.inventory.items().find(i => acceptable.includes(i.name))
                    if (tool) await bot.equip(tool, 'hand')
                }
                return ok2
            }
        }
        console.log(`[Craft] 材料不足，無法合成 ${toolName}`)
        return false
    }

    const chosen = autoChoose ? craftable[0] : await chooseCraft(bot, toolSuffix.slice(1), craftable)
    const ok = await _craft(bot, chosen, table)
    await _reclaimCraftingTable(bot)
    if (ok) {
        const tool = bot.inventory.items().find(i => acceptable.includes(i.name))
        if (tool) await bot.equip(tool, 'hand')
    }
    return ok
}

async function ensureAxe(bot)                      { return _ensureTool(bot, '_axe') }
async function ensurePickaxe(bot)                  { return _ensureTool(bot, '_pickaxe') }
async function ensureShovel(bot)                   { return _ensureTool(bot, '_shovel') }
async function ensureSword(bot)                    { return _ensureTool(bot, '_sword', null, true) }
async function ensurePickaxeTier(bot, minTier)     { return _ensureTool(bot, '_pickaxe', minTier) }

const _BLOCK_TOOL = [
    ['_shovel',  ['dirt', 'sand', 'gravel', 'grass_block', 'podzol']],
    ['_pickaxe', ['cobblestone', 'stone', 'ore', 'deepslate', 'tuff']],
    ['_axe',     ['log', 'planks', 'wood', 'crafting_table']],
]

// 根據方塊類型確保有對應工具（不夠就合成），並裝備到手上
async function ensureToolFor(bot, blockName) {
    for (const [suffix, patterns] of _BLOCK_TOOL) {
        if (patterns.some(p => blockName.includes(p))) {
            const ok = await _ensureTool(bot, suffix)
            const tool = bot.inventory.items().find(i => i.name.endsWith(suffix))
            if (tool) await bot.equip(tool, 'hand')
            return ok
        }
    }
    return true
}

// 把背包裡的原木轉成木板，maxLogs 限制最多轉幾根（預設全部）
async function convertLogsToPlanks(bot, maxLogs = Infinity) {
    for (const [log, plank] of Object.entries(LOG_TO_PLANK)) {
        const stacks = bot.inventory.items().filter(i => i.name === log)
        if (stacks.length === 0) continue
        const total = stacks.reduce((s, i) => s + i.count, 0)
        const toConvert = Math.min(total, maxLogs)

        const plankId = bot.registry.itemsByName[plank]?.id
        if (!plankId) continue
        const recipe = bot.recipesFor(plankId, null, 1, null)[0]
        if (!recipe) continue
        try {
            await bot.craft(recipe, toConvert)
            console.log(`[Craft] ${log} x${toConvert} → ${plank}`)
            // 撿起附近掉落的木板（背包滿時會掉地上）
            await _sleep(300)
            await _collectNearby(bot, 4)
        } catch (e) {
            console.log(`[Craft] 轉換木板失敗: ${e.message}`)
        }
    }
}

// 確保附近有工作檯（找不到就合成並放置）
async function ensureCraftingTable(bot) {
    const tableBlockId = bot.registry.blocksByName['crafting_table']?.id
    if (!tableBlockId) return null

    let table = bot.findBlock({ matching: tableBlockId, maxDistance: 6 })
    if (table) {
        try {
            await bot.pathfinder.goto(
                new goals.GoalNear(table.position.x, table.position.y, table.position.z, 2)
            )
        } catch (e) {
            console.log(`[Craft] 導航到工作檯失敗: ${e.message}`)
            return null
        }
        return table
    }

    if (!bot.inventory.items().some(i => i.name === 'crafting_table')) {
        if (_countPlanks(bot) < 4) {
            console.log('[Craft] 木板不夠，無法製作工作檯')
            return null
        }
        const ok = await _craft(bot, 'crafting_table', null)
        if (!ok) return null
    }

    return await _placeCraftingTable(bot, tableBlockId)
}

async function compactCompressibleItems(bot) {
    const craftPlan = []
    for (const [itemName, blockName] of Object.entries(COMPACTABLE_ITEMS)) {
        const total = bot.inventory.items()
            .filter(i => i.name === itemName)
            .reduce((sum, i) => sum + i.count, 0)
        const craftCount = Math.floor(total / 9)
        if (craftCount <= 0) continue

        const blockItem = bot.registry.itemsByName[blockName]
        if (!blockItem) continue
        craftPlan.push({ itemName, blockName, craftCount, blockItem })
    }

    if (craftPlan.length === 0) return 0

    const table = await ensureCraftingTable(bot)
    if (!table) {
        console.log('[Craft] 無法取得工作檯，略過資源壓縮')
        return 0
    }

    let craftedTotal = 0
    try {
        for (const plan of craftPlan) {
            const recipe = bot.recipesFor(plan.blockItem.id, null, 1, table)[0]
            if (!recipe) {
                console.log(`[Craft] 找不到 ${plan.blockName} 的配方，略過`)
                continue
            }

            let craftedThisItem = 0
            for (let i = 0; i < plan.craftCount; i++) {
                try {
                    await bot.craft(recipe, 1, table)
                    craftedThisItem++
                    craftedTotal++
                    await _sleep(250)
                } catch (e) {
                    console.log(`[Craft] 壓縮 ${plan.itemName} 失敗: ${e.message}`)
                    break
                }
            }

            if (craftedThisItem > 0) {
                console.log(`[Craft] 壓縮 ${plan.itemName} x${craftedThisItem * 9} → ${plan.blockName} x${craftedThisItem}`)
            }
        }
    } finally {
        await _reclaimCraftingTable(bot)
    }

    return craftedTotal
}

async function _placeCraftingTable(bot, tableBlockId) {
    const item = bot.inventory.items().find(i => i.name === 'crafting_table')
    if (!item) return null
    if (Date.now() - _lastTeleportLikeAt < 1200) {
        await _sleep(800)
    }
    await bot.equip(item, 'hand')

    const pos = bot.entity.position.floored()
    const dirs = [[1,0],[0,1],[-1,0],[0,-1]]

    // 先找不需要挖的位置，再找需要挖的
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
        if (_isOccupiedByEntity(bot, spacePos)) continue

        const space = bot.blockAt(spacePos)
        const isOpen = space && REPLACEABLE_BLOCKS.has(space.name)
        candidates.push({ ground, spacePos, needDig: !isOpen, space })
    }

    // 已是空氣的優先，其次才是需要挖的
    candidates.sort((a, b) => a.needDig - b.needDig)

    for (const { ground, spacePos, needDig, space } of candidates) {
        if (needDig) {
            if (!space || space.boundingBox !== 'block') continue
            try {
                const tool = bot.pathfinder.bestHarvestTool(space)
                if (tool) await bot.equip(tool, 'hand')
            } catch (_) {}
            try { await bot.dig(space) } catch (_) { continue }
            const fresh = bot.blockAt(spacePos)
            if (!fresh || !REPLACEABLE_BLOCKS.has(fresh.name)) continue
        }

        try {
            const freshItem = bot.inventory.items().find(i => i.name === 'crafting_table')
            if (!freshItem) break
            await bot.equip(freshItem, 'hand')
            await bot.lookAt(ground.position.offset(0.5, 1, 0.5))
            await bot.placeBlock(ground, new Vec3(0, 1, 0))
            await _sleep(400)
            const placed = bot.blockAt(spacePos)
            if (placed && placed.type === tableBlockId) {
                console.log('[Craft] 放置工作檯')
                _placedTablePos = spacePos.clone()
                return placed
            }
        } catch (e) {
            console.log('[Craft] 放置工作檯失敗:', e.message)
        }
    }

    console.log('[Craft] 找不到放置位置')
    return null
}

function _isOccupiedByEntity(bot, pos) {
    return Object.values(bot.entities).some(e => {
        if (!e?.position) return false
        const dx = Math.abs(e.position.x - (pos.x + 0.5))
        const dy = Math.abs(e.position.y - pos.y)
        const dz = Math.abs(e.position.z - (pos.z + 0.5))
        return dx < 0.8 && dy < 1.8 && dz < 0.8
    })
}

async function _reclaimCraftingTable(bot) {
    if (!_placedTablePos) return
    const pos = _placedTablePos
    _placedTablePos = null
    const block = bot.blockAt(pos)
    if (!block || block.name !== 'crafting_table') return
    try {
        await ensureAxe(bot)
        await bot.dig(block)
        for (let attempt = 0; attempt < 8; attempt++) {
            await _sleep(500)
            if (bot.inventory.items().some(i => i.name === 'crafting_table')) break
            const dropped = Object.values(bot.entities).find(
                e => e.name === 'item' && e.position.distanceTo(pos) < 3
            )
            if (dropped) {
                try {
                    await bot.pathfinder.goto(
                        new goals.GoalNear(dropped.position.x, dropped.position.y, dropped.position.z, 0)
                    )
                } catch (_) {}
            }
        }
        if (bot.inventory.items().some(i => i.name === 'crafting_table')) {
            console.log('[Craft] 回收工作檯')
        } else {
            console.log('[Craft] 無法回收工作檯')
        }
    } catch (e) {
        console.log('[Craft] 回收工作檯失敗:', e.message)
    }
}

const _ORE_TO_INGOT = {
    raw_iron: 'iron_ingot',         iron_ore: 'iron_ingot',         deepslate_iron_ore: 'iron_ingot',
    raw_gold: 'gold_ingot',         gold_ore: 'gold_ingot',         deepslate_gold_ore: 'gold_ingot',
    raw_copper: 'copper_ingot',     copper_ore: 'copper_ingot',     deepslate_copper_ore: 'copper_ingot',
}
const _TOOL_INGOT = { _pickaxe: 'iron_ingot', _axe: 'iron_ingot', _shovel: 'iron_ingot', _sword: 'iron_ingot' }

// 若工具合成缺鐵錠但背包有原礦，自動燒製所需數量後回傳 true
async function _smeltIfNeeded(bot, toolSuffix) {
    const neededIngot = _TOOL_INGOT[toolSuffix]
    if (!neededIngot) return false

    const oreEntry = bot.inventory.items().find(i => _ORE_TO_INGOT[i.name] === neededIngot)
    if (!oreEntry) return false

    const hasFurnace = !!bot.findBlock({
        matching: b => ['furnace', 'lit_furnace', 'blast_furnace']
            .map(n => bot.registry.blocksByName[n]?.id).filter(Boolean).includes(b.type),
        maxDistance: 32,
    })
    const cobble = bot.inventory.items().filter(i => i.name === 'cobblestone').reduce((s, i) => s + i.count, 0)
    if (!hasFurnace && cobble < 8) {
        console.log('[Craft] 無熔爐且圓石不足，跳過燒製')
        return false
    }

    const needed = toolSuffix === '_shovel' ? 1 : 3
    const target = oreEntry.name.includes('iron') ? 'iron'
                 : oreEntry.name.includes('gold') ? 'gold' : 'copper'

    console.log(`[Craft] 有 ${oreEntry.name}，先燒製 ${needed} 個 ${neededIngot}...`)
    const { startSmelting, isActive: isSmeltingActive } = require('./smelting')
    const { resumeMining } = require('./mining')
    startSmelting(bot, { target, count: needed })  // 內部會停 mining

    while (isSmeltingActive()) {
        await _sleep(3000)
    }
    const ingotCount = bot.inventory.items()
        .filter(i => i.name === neededIngot)
        .reduce((s, i) => s + i.count, 0)
    resumeMining()
    return ingotCount >= needed
}

const _MATERIAL_FOR_TOOL_PREFIX = {
    diamond: 'diamond',
    iron:    'iron_ingot',
    gold:    'gold_ingot',
    golden:  'gold_ingot',
}

async function _decompressIfNeeded(bot, acceptable) {
    for (const toolName of acceptable) {
        const prefix = toolName.split('_')[0]
        const material = _MATERIAL_FOR_TOOL_PREFIX[prefix]
        if (!material) continue

        const blockName = COMPACTABLE_ITEMS[material]
        if (!blockName) continue
        if (!bot.inventory.items().some(i => i.name === blockName)) continue

        const materialId = bot.registry.itemsByName[material]?.id
        if (!materialId) continue

        const recipe = bot.recipesFor(materialId, null, 1, null)[0]
        if (!recipe) {
            console.log(`[Craft] 找不到 ${blockName} → ${material} 的解壓縮配方`)
            continue
        }

        try {
            await bot.craft(recipe, 1, null)
            console.log(`[Craft] 解壓縮 ${blockName} → ${material} x9`)
            return true
        } catch (e) {
            console.log(`[Craft] 解壓縮 ${blockName} 失敗: ${e.message}`)
        }
    }
    return false
}

async function _craft(bot, itemName, craftingTable) {
    const item = bot.registry.itemsByName[itemName]
    if (!item) return false
    const recipe = bot.recipesFor(item.id, null, 1, craftingTable)[0]
    if (!recipe) {
        console.log(`[Craft] 找不到 ${itemName} 的配方`)
        return false
    }
    try {
        await bot.craft(recipe, 1, craftingTable)
        console.log(`[Craft] 合成了 ${itemName}`)
        return true
    } catch (e) {
        console.log(`[Craft] 合成 ${itemName} 失敗: ${e.message}`)
        if (bot.inventory.items().length >= 36) {
            console.log('[Craft] 背包已滿，執行整理...')
            const { handleFull } = require('./inventory')
            await handleFull(bot)
        }
        return false
    }
}

async function _collectNearby(bot, maxDistance) {
    const items = Object.values(bot.entities).filter(
        e => e.name === 'item' && e.position.distanceTo(bot.entity.position) < maxDistance
    )
    for (const e of items) {
        try {
            await bot.pathfinder.goto(
                new goals.GoalNear(e.position.x, e.position.y, e.position.z, 1)
            )
            await _sleep(200)
        } catch (_) {}
    }
}

function _countPlanks(bot) {
    return bot.inventory.items()
        .filter(i => i.name.endsWith('_planks'))
        .reduce((s, i) => s + i.count, 0)
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

function noteTeleportLikeAction() {
    _lastTeleportLikeAt = Date.now()
}

module.exports = { ensureAxe, ensurePickaxe, ensureShovel, ensureSword, ensurePickaxeTier, ensureToolFor, convertLogsToPlanks, ensureCraftingTable, reclaimCraftingTable: _reclaimCraftingTable, compactCompressibleItems, chooseCraft, applyCraftDecision, noteTeleportLikeAction }
