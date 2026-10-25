const bridge = require('./bridge')
const activityStack = require('./activity')
const execution = require('./execution')
const INVENTORY_FULL = 34
let _decision = null
let _checking = false
let _blockedSignature = null
let _generation = 0
const DISPOSABLE = new Set(['cobblestone', 'cobbled_deepslate', 'diorite', 'andesite', 'granite',
    'tuff', 'gravel', 'dirt', 'coarse_dirt', 'netherrack', 'flint', 'rotten_flesh',
    'spider_eye', 'bone', 'clay_ball', 'sand', 'calcite'])
const BUILDING = new Set(['cobblestone', 'cobbled_deepslate', 'dirt', 'netherrack'])
function signature(bot) {
    return bot.inventory.items().map(i => `${i.slot}:${i.name}:${i.count}`).sort().join('|')
}
function applyInventoryDecision(decision) { _decision = decision }
async function dropItems(bot, items) {
    const seen = new Set()
    for (const request of items || []) {
        if (!execution.isCurrent()) return
        if (!request || !DISPOSABLE.has(request.name) || seen.has(request.name) || !Number.isInteger(request.count) || request.count <= 0) continue
        seen.add(request.name)
        const item = bot.inventory.items().find(i => i.name === request.name)
        if (!item) continue
        const total = bot.inventory.items().filter(i => i.name === request.name).reduce((n, i) => n + i.count, 0)
        const count = Math.min(request.count, Math.max(0, total - (BUILDING.has(request.name) ? 64 : 0)))
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
