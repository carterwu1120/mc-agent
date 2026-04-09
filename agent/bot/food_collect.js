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

activityStack.register('getfood', _pause)

function _pause(_bot) {
    isGettingFood = false
    _isPaused = true
    console.log('[Food] 暫停蒐集食物')
}

async function startGetFood(bot, goal = {}) {
    if (isGettingFood) {
        console.log('[Food] 已在蒐集食物中')
        return
    }
    isGettingFood = true
    _startingCookedCount = _countItems(bot, COOKED_FOOD_ITEMS)
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
    console.log('[Food] 停止蒐集食物')
}

function isActive() {
    return isGettingFood
}

async function _loop(bot, goal = {}) {
    _isPaused = false
    const targetCooked = goal.count ?? 8

    while (isGettingFood) {
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
        console.log(`[Food] 有生食 ${rawEntry.name} x${toCook}，交由上層冶煉`)
        isGettingFood = false
        bridge.sendState(bot, 'activity_stuck', {
            activity: 'getfood',
            reason: 'has_raw_food',
            raw_food: rawEntry.name,
            raw_count: toCook,
            remaining,
        })
        break
    }

    if (!_isPaused && !isGettingFood) activityStack.pop(bot)
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
