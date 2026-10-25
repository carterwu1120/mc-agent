const bridge = require('./bridge')
const activityStack = require('./activity')
const execution = require('./execution')
const INVENTORY_FULL = 34
let _decision = null
let _checking = false
let _blockedSignature = null
let _generation = 0
// Last line of defence for any drop request (rules, LLM or skill). Keep in sync with
// is_guarded() in agent/handlers/inventory.py.
const TOOL_SUFFIXES = ['_pickaxe', '_axe', '_shovel', '_hoe', '_sword', '_helmet', '_chestplate',
    '_leggings', '_boots', '_horse_armor']
const GUARDED = new Set(['bow', 'crossbow', 'trident', 'shield', 'fishing_rod', 'shears',
    'flint_and_steel', 'compass', 'clock', 'elytra', 'bucket', 'water_bucket', 'lava_bucket', 'arrow',
    'bread', 'apple', 'golden_apple', 'enchanted_golden_apple', 'golden_carrot', 'carrot', 'potato',
    'baked_potato', 'beetroot', 'melon_slice', 'sweet_berries', 'cookie', 'pumpkin_pie',
    'mushroom_stew', 'beetroot_soup', 'rabbit_stew', 'dried_kelp',
    'beef', 'porkchop', 'chicken', 'mutton', 'rabbit', 'cod', 'salmon',
    'coal', 'charcoal', 'coal_block', 'blaze_rod', 'stick',
    'crafting_table', 'furnace', 'chest', 'torch', 'string',
    'diamond', 'emerald', 'lapis_lazuli', 'redstone', 'quartz', 'amethyst_shard', 'netherite_scrap',
    'ancient_debris', 'diamond_block', 'emerald_block', 'lapis_block', 'redstone_block', 'iron_block',
    'gold_block', 'copper_block', 'netherite_block'])
function isGuarded(name) {
    return GUARDED.has(name) || TOOL_SUFFIXES.some(s => name.endsWith(s)) || name.startsWith('cooked_') ||
        name.startsWith('raw_') || ['_ore', '_ingot', '_nugget', '_log', '_wood', '_planks'].some(s => name.endsWith(s))
}
// Placeable blocks for pillaring/bridging: at least this many stay in total.
const BUILDING = new Set(['cobblestone', 'cobbled_deepslate', 'dirt', 'netherrack', 'stone'])
const BUILDING_RESERVE = 128
function signature(bot) {
    return bot.inventory.items().map(i => `${i.slot}:${i.name}:${i.count}`).sort().join('|')
}
function applyInventoryDecision(decision) { _decision = decision }
async function dropItems(bot, items) {
    // Face back the way we came: dropped stacks land behind the bot, not on the path
    // the interrupted activity continues along (items can be picked up after ~2 s).
    try { await bot.look(bot.entity.yaw + Math.PI, 0.3, true) } catch (_) {}
    if (!execution.isCurrent()) return
    const seen = new Set()
    const totalOf = name => bot.inventory.items().filter(i => i.name === name).reduce((n, i) => n + i.count, 0)
    let buildingSpare = Math.max(0, [...BUILDING].reduce((n, name) => n + totalOf(name), 0) - BUILDING_RESERVE)
    for (const request of items || []) {
        if (!execution.isCurrent()) return
        if (!request || isGuarded(request.name) || seen.has(request.name) || !Number.isInteger(request.count) || request.count <= 0) continue
        seen.add(request.name)
        const item = bot.inventory.items().find(i => i.name === request.name)
        if (!item) continue
        let count = Math.min(request.count, totalOf(request.name))
        if (BUILDING.has(request.name)) {
            count = Math.min(count, buildingSpare)
            buildingSpare -= count
        }
        if (count > 0) await bot.toss(item.type, null, count)
    }
}
async function _handleFull(bot) {
    if (!execution.isCurrent() || _checking || signature(bot) === _blockedSignature) return
    _checking = true
    _decision = null
    const generation = ++_generation
    try {
        activityStack.pause(bot)
        bot.pathfinder?.setGoal(null)
        bot.clearControlStates?.()
        activityStack.waitFor('inventory_recovery', 120000)
        bridge.sendState(bot, 'inventory_full', {})
        const decision = await _waitForDecision(120000, generation)
        if (generation !== _generation || !execution.isCurrent()) return
        if (decision?.action === 'drop') await dropItems(bot, decision.items)
        if (generation !== _generation || !execution.isCurrent()) return
        if (bot.inventory.items().length < INVENTORY_FULL) {
            _blockedSignature = null
            activityStack.resumeCurrent(bot)
        } else {
            _blockedSignature = signature(bot)
            bridge.sendState(bot, 'activity_stuck', { activity: activityStack.getActivity(),
                reason: 'inventory_blocked', detail: '背包仍滿，沒有可安全清除的空間，等待玩家整理。' })
        }
    } finally {
        if (generation === _generation) _checking = false
    }
}
function _waitForDecision(timeout, generation) {
    return new Promise(resolve => {
        const finish = value => { clearInterval(poll); clearTimeout(timer); resolve(value) }
        const poll = setInterval(() => {
            if (generation !== _generation || !execution.isCurrent()) return finish(null)
            if (_decision !== null) { const result = _decision; _decision = null; finish(result) }
        }, 200)
        const timer = setTimeout(() => finish(null), timeout)
    })
}
function checkFull(bot) {
    if (bot.inventory.items().length < INVENTORY_FULL) { _blockedSignature = null; return false }
    _handleFull(bot).catch(error => {
        _blockedSignature = signature(bot)
        bridge.sendState(bot, 'activity_stuck', { activity: activityStack.getActivity(), reason: 'inventory_blocked', detail: error.message })
    })
    return true
}
function startMonitor(bot) {
    bot.on('playerCollect', collector => { if (collector.username === bot.username) checkFull(bot) })
    console.log('[Inv] 原地背包整理已啟動')
}
async function waitUntilIdle() { while (_checking) await new Promise(r => setTimeout(r, 100)) }
module.exports = {
    cancelRecovery() { ++_generation; _checking = false; _decision = null },
    startMonitor, checkFull, waitUntilIdle, applyInventoryDecision,
    // Kept as an API alias for existing callers; no digging, crafting or burial.
    buryItems: (bot, items) => dropItems(bot, [...items].map(([name, count]) => ({ name, count }))),
    tidyInventory: _handleFull, handleFull: _handleFull, dropItems,
}
