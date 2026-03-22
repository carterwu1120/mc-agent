const { goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const bridge = require('./bridge')

let _craftDecision = null
let _placedTablePos = null

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

const TOOL_PRIORITY = {
    _axe:     ['diamond_axe',     'golden_axe',     'iron_axe',     'stone_axe',     'wooden_axe'],
    _pickaxe: ['diamond_pickaxe', 'iron_pickaxe',   'stone_pickaxe', 'wooden_pickaxe'],
    _shovel:  ['diamond_shovel',  'iron_shovel',    'stone_shovel',  'wooden_shovel'],
}

// 通用工具合成：確保背包有指定類型的工具
async function _ensureTool(bot, toolSuffix) {
    if (bot.inventory.items().some(i => i.name.endsWith(toolSuffix))) return true
    const toolName = toolSuffix.slice(1)  // '_axe' → 'axe'
    console.log(`[Craft] 沒有 ${toolName}，開始合成...`)

    await convertLogsToPlanks(bot, 3)
    const table = await ensureCraftingTable(bot)

    const stickCount = bot.inventory.items()
        .filter(i => i.name === 'stick')
        .reduce((s, i) => s + i.count, 0)
    if (stickCount < 2) await _craft(bot, 'stick', null)

    const priority = TOOL_PRIORITY[toolSuffix] ?? []
    const craftable = priority.filter(name => {
        const item = bot.registry.itemsByName[name]
        if (!item || !table) return false
        return bot.recipesFor(item.id, null, 1, table).length > 0
    })

    if (craftable.length === 0) {
        console.log(`[Craft] 材料不足，無法合成 ${toolName}`)
        return false
    }

    const chosen = await chooseCraft(bot, `${toolName}`, craftable)
    const ok = await _craft(bot, chosen, table)
    await _reclaimCraftingTable(bot)
    if (ok) {
        const tool = bot.inventory.items().find(i => i.name.endsWith(toolSuffix))
        if (tool) await bot.equip(tool, 'hand')
    }
    return ok
}

async function ensureAxe(bot)     { return _ensureTool(bot, '_axe') }
async function ensurePickaxe(bot) { return _ensureTool(bot, '_pickaxe') }
async function ensureShovel(bot)  { return _ensureTool(bot, '_shovel') }

const _BLOCK_TOOL = [
    ['_shovel',  ['dirt', 'sand', 'gravel', 'grass_block', 'podzol']],
    ['_pickaxe', ['cobblestone', 'stone', 'ore', 'deepslate']],
    ['_axe',     ['log', 'planks', 'wood', 'crafting_table']],
]

// 根據方塊類型確保有對應工具（不夠就合成），並裝備到手上
async function ensureToolFor(bot, blockName) {
    for (const [suffix, patterns] of _BLOCK_TOOL) {
        if (patterns.some(p => blockName.includes(p))) {
            await _ensureTool(bot, suffix)
            const tool = bot.inventory.items().find(i => i.name.endsWith(suffix))
            if (tool) await bot.equip(tool, 'hand')
            return
        }
    }
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
        await bot.pathfinder.goto(
            new goals.GoalNear(table.position.x, table.position.y, table.position.z, 2)
        )
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

async function _placeCraftingTable(bot, tableBlockId) {
    const item = bot.inventory.items().find(i => i.name === 'crafting_table')
    if (!item) return null
    await bot.equip(item, 'hand')

    const pos = bot.entity.position.floored()
    const dirs = [[1,0],[0,1],[-1,0],[0,-1]]

    for (const [dx, dz] of dirs) {
        const ground = bot.blockAt(pos.offset(dx, -1, dz))
        const space  = bot.blockAt(pos.offset(dx,  0, dz))
        if (!ground || ground.boundingBox !== 'block') continue
        if (!space  || space.name !== 'air') continue

        try {
            await bot.lookAt(ground.position.offset(0.5, 1, 0.5))
            await bot.placeBlock(ground, new Vec3(0, 1, 0))
            await _sleep(400)
            const placed = bot.findBlock({ matching: tableBlockId, maxDistance: 4 })
            if (placed) {
                console.log('[Craft] 放置工作檯')
                _placedTablePos = placed.position.clone()
                return placed
            }
        } catch (e) {
            console.log('[Craft] 放置工作檯失敗:', e.message)
        }
    }

    console.log('[Craft] 找不到放置位置')
    return null
}

async function _reclaimCraftingTable(bot) {
    if (!_placedTablePos) return
    const pos = _placedTablePos
    _placedTablePos = null
    const block = bot.blockAt(pos)
    if (!block || block.name !== 'crafting_table') return
    try {
        const axe = bot.inventory.items().find(i => i.name.endsWith('_axe'))
        if (axe) await bot.equip(axe, 'hand')
        await bot.dig(block)
        console.log('[Craft] 回收工作檯')
    } catch (e) {
        console.log('[Craft] 回收工作檯失敗:', e.message)
    }
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

module.exports = { ensureAxe, ensurePickaxe, ensureShovel, ensureToolFor, convertLogsToPlanks, ensureCraftingTable, chooseCraft, applyCraftDecision }
