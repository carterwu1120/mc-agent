const execution = require('./execution')
const activityStack = require('./activity')
const bridge = require('./bridge')
const { RAW_FOOD_ITEMS } = require('./eating')

const COOKED_FOOD_ITEMS = new Set([
    'cooked_beef', 'cooked_porkchop', 'cooked_chicken', 'cooked_mutton',
    'cooked_rabbit', 'cooked_cod', 'cooked_salmon', 'baked_potato', 'bread',
])

const RAW_TO_COOKED = {
    beef: 'cooked_beef',
    porkchop: 'cooked_porkchop',
    chicken: 'cooked_chicken',
    mutton: 'cooked_mutton',
    rabbit: 'cooked_rabbit',
    cod: 'cooked_cod',
    salmon: 'cooked_salmon',
    potato: 'baked_potato',
}

let isGettingFood = false
let _isPaused = false
let _startingCookedCount = 0
let _loopGen = 0
// Cooked count when the last cooking round started; a round that adds nothing stops.
let _cookedBeforeRound = null

activityStack.register('getfood', _pause)

function _pause(_bot) {
    _loopGen++
    isGettingFood = false
    _isPaused = true
    console.log('[Food] 暫停蒐集食物')
}

async function startGetFood(bot, goal = {}) {
    if (isGettingFood) {
        console.log('[Food] 已在蒐集食物中')
        return
    }
    const targetCooked = Number(goal.count)
    if (!Number.isInteger(targetCooked) || targetCooked <= 0) {
        console.log('[Food] 缺少有效的熟食數量，拒絕啟動 getfood')
        bridge.sendState(bot, 'activity_stuck', {
            activity: 'getfood', reason: 'invalid_goal', goal,
        })
        return
    }
    isGettingFood = true
    _startingCookedCount = _countItems(bot, COOKED_FOOD_ITEMS)
    _cookedBeforeRound = null
    activityStack.push(bot, 'getfood', goal, (b) => _resumeGetFood(b, goal))
    console.log(`[Food] 開始蒐集食物 goal=${JSON.stringify(goal)}`)
    _loop(bot, goal)
}

function _resumeGetFood(bot, originalGoal) {
    if (isGettingFood) return
    isGettingFood = true
    activityStack.updateTopGoal(originalGoal)
    console.log('[Food] 恢復蒐集食物')
    _loop(bot, originalGoal)
}

function stopGetFood(_bot) {
    if (!isGettingFood) return
    isGettingFood = false
    _isPaused = false
    _startingCookedCount = 0
    _loopGen++
    console.log('[Food] 停止蒐集食物')
}

function isActive() {
    return isGettingFood
}

async function _loop(bot, goal = {}) {
    _isPaused = false
    const _myGen = ++_loopGen
    const targetCooked = goal.count

    while (isGettingFood && execution.isCurrent() && _loopGen === _myGen) {
        const cookedCount = _countItems(bot, COOKED_FOOD_ITEMS)
        const cookedProduced = Math.max(0, cookedCount - _startingCookedCount)

        if (cookedProduced >= targetCooked) {
            console.log(`[Food] 已完成蒐集熟食 ${cookedProduced}/${targetCooked}`)
            isGettingFood = false
            bridge.sendState(bot, 'activity_done', { activity: 'getfood' })
            break
        }

        const rawEntry = _findRawFood(bot)
        if (!rawEntry) {
            const remaining = Math.max(1, targetCooked - cookedProduced)
            console.log(`[Food] 背包沒有原始食材，無法繼續（還需 ${remaining} 個熟食）`)
            isGettingFood = false
            bridge.sendState(bot, 'activity_stuck', { activity: 'getfood', reason: 'no_raw_food', remaining })
            break
        }

        const remaining = Math.max(1, targetCooked - cookedProduced)
        const toCook = Math.min(rawEntry.count, remaining)
        if (_cookedBeforeRound !== null && cookedCount <= _cookedBeforeRound) {
            // Smelting already reported its own reason (fuel, furnace spot); do not loop.
            console.log('[Food] 上一輪烹飪沒有產出熟食，停止')
            isGettingFood = false
            bridge.sendState(bot, 'activity_stuck', {
                activity: 'getfood', reason: 'cook_failed', raw_food: rawEntry.name, remaining,
            })
            break
        }
        // getfood cooks what it carries. It used to report has_raw_food and leave cooking
        // to the planner, which cost an LLM recovery (and budget) on every food plan.
        _cookedBeforeRound = cookedCount
        console.log(`[Food] 烤 ${rawEntry.name} x${toCook}`)
        require('./smelting').startSmelting(bot, { target: rawEntry.name, count: toCook })
        if (activityStack.getActivity() !== 'smelting') {
            console.log('[Food] 無法開始烹飪（熔爐流程已在使用中）')
            isGettingFood = false
            bridge.sendState(bot, 'activity_stuck', {
                activity: 'getfood', reason: 'cook_failed', raw_food: rawEntry.name, remaining,
            })
        }
        // Otherwise smelting was pushed above getfood (pausing this loop); its pop
        // resumes getfood, which re-counts cooked food.
        break
    }

    if (!execution.isCurrent() || _loopGen !== _myGen) return
    if (!_isPaused && !isGettingFood && _loopGen === _myGen) activityStack.pop(bot)
    _isPaused = false
}

function _countItems(bot, namesSet) {
    return bot.inventory.items()
        .filter(i => namesSet.has(i.name))
        .reduce((sum, i) => sum + i.count, 0)
}

function _findRawFood(bot) {
    return bot.inventory.items()
        .filter(i => RAW_FOOD_ITEMS.has(i.name) && RAW_TO_COOKED[i.name])
        .sort((a, b) => b.count - a.count)[0] ?? null
}

module.exports = { startGetFood, stopGetFood, isActive }

// Each activity has its own cancellable async scope, including nested activities.
for (const [name, fn] of Object.entries(module.exports)) {
    if (name.startsWith('start') && name !== 'startMonitor') module.exports[name] = execution.owned(fn)
}
