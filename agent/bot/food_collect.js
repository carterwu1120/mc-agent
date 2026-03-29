const activityStack = require('./activity')
const bridge = require('./bridge')
const { RAW_FOOD_ITEMS } = require('./eating')
const { startHunting, stopHunting, isActive: isHuntingActive } = require('./hunting')
const { startSmelting, stopSmelting, isActive: isSmeltingActive } = require('./smelting')
const { startFishing, stopFishing, isActive: isFishingActive } = require('./fishing')

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
let _huntAttempts = 0
let _lastSmeltPlan = null
let _blockedRawTargets = new Set()
let _awaitingRecovery = false
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
    _huntAttempts = 0
    _lastSmeltPlan = null
    _blockedRawTargets = new Set()
    _awaitingRecovery = false
    _startingCookedCount = _countItems(bot, COOKED_FOOD_ITEMS)
    activityStack.push(bot, 'getfood', goal, (b) => _resumeGetFood(b, goal))
    console.log(`[Food] 開始蒐集食物 goal=${JSON.stringify(goal)}`)
    _loop(bot, goal)
}

function _resumeGetFood(bot, originalGoal) {
    if (isGettingFood) return
    isGettingFood = true
    if (_awaitingRecovery) {
        _awaitingRecovery = false
        _blockedRawTargets.clear()
        _lastSmeltPlan = null
    }
    activityStack.updateTopGoal(originalGoal)
    console.log('[Food] 恢復蒐集食物')
    _loop(bot, originalGoal)
}

function stopGetFood(_bot) {
    if (!isGettingFood) return
    isGettingFood = false
    _isPaused = false
    _huntAttempts = 0
    _lastSmeltPlan = null
    _blockedRawTargets = new Set()
    _awaitingRecovery = false
    _startingCookedCount = 0
    console.log('[Food] 停止蒐集食物')
}

function isActive() {
    return isGettingFood
}

async function _loop(bot, goal = {}) {
    _isPaused = false
    const targetCooked = goal.count ?? 8

    _resolveLastSmeltAttempt(bot)

    while (isGettingFood) {
        const cookedCount = _countItems(bot, COOKED_FOOD_ITEMS)
        const cookedProduced = Math.max(0, cookedCount - _startingCookedCount)
        if (cookedProduced >= targetCooked) {
            console.log(`[Food] 已完成蒐集熟食 ${cookedProduced}/${targetCooked}（背包現有 ${cookedCount}）`)
            isGettingFood = false
            bridge.sendState(bot, 'activity_done', { activity: 'getfood', reason: 'goal_reached' })
            break
        }

        const rawEntry = _findRawFood(bot)
        if (rawEntry) {
            if (_blockedRawTargets.has(rawEntry.name)) {
                console.log(`[Food] ${rawEntry.name} 目前無法燒熟，等待 recovery 動作完成後再重試`)
                isGettingFood = false
                _isPaused = true
                _awaitingRecovery = true
                break
            }
            const remaining = Math.max(1, targetCooked - cookedProduced)
            const toCook = Math.min(rawEntry.count, remaining)
            console.log(`[Food] 發現 ${rawEntry.name} x${rawEntry.count}，先燒熟 ${toCook} 個`)
            _lastSmeltPlan = {
                rawName: rawEntry.name,
                cookedName: RAW_TO_COOKED[rawEntry.name],
                rawBefore: _countNamedItem(bot, rawEntry.name),
                cookedBefore: _countNamedItem(bot, RAW_TO_COOKED[rawEntry.name]),
            }
            startSmelting(bot, { target: rawEntry.name, count: toCook })
            break
        }

        if (_huntAttempts === 0) {
            const huntsNeeded = Math.max(1, Math.min(3, Math.ceil((targetCooked - cookedProduced) / 2)))
            console.log(`[Food] 先去狩獵，目標 ${huntsNeeded} 隻動物`)
            _huntAttempts++
            startHunting(bot, { count: huntsNeeded })
            break
        }

        console.log('[Food] 改用釣魚補充食物')
        startFishing(bot, { catches: Math.max(2, targetCooked - cookedProduced) })
        break
    }

    if (!_isPaused && isGettingFood) {
        // 若子活動沒有成功啟動，避免 getfood 卡住。
        if (!isHuntingActive() && !isSmeltingActive() && !isFishingActive()) {
            const rawEntry = _findRawFood(bot)
            if (!rawEntry) {
                console.log('[Food] 無法取得更多食物，停止')
                isGettingFood = false
                bridge.sendState(bot, 'activity_stuck', { activity: 'getfood', reason: 'no_food_source' })
            }
        }
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

function _countNamedItem(bot, itemName) {
    return bot.inventory.items()
        .filter(i => i.name === itemName)
        .reduce((sum, i) => sum + i.count, 0)
}

function _resolveLastSmeltAttempt(bot) {
    if (!_lastSmeltPlan) return

    const plan = _lastSmeltPlan
    _lastSmeltPlan = null

    const currentRaw = _countNamedItem(bot, plan.rawName)
    const currentCooked = _countNamedItem(bot, plan.cookedName)
    const rawConsumed = Math.max(0, plan.rawBefore - currentRaw)
    const cookedGained = Math.max(0, currentCooked - plan.cookedBefore)

    if (rawConsumed > 0 || cookedGained > 0) {
        console.log(`[Food] ${plan.rawName} 已成功燒製進展 raw -${rawConsumed}, cooked +${cookedGained}`)
        return
    }

    _blockedRawTargets.add(plan.rawName)
    console.log(`[Food] ${plan.rawName} 燒製失敗且沒有進展，避免重複嘗試`)
}

module.exports = { startGetFood, stopGetFood, isActive }
