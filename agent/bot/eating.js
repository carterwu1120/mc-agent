const bridge = require('./bridge')
const { getActivity } = require('./activity')

const FOOD_THRESHOLD = 18     // 食物低於此值就吃東西
const FOOD_LOW_THRESHOLD = 10 // 食物低於此值且無食物時發送 food_low 事件
const FOOD_LOW_COOLDOWN = 30000
const EAT_DEFER_ACTIVITIES = new Set([
    'hunting',
    'combat',
    'mining',
    'chopping',
])

// 可以放進熔爐的生食（需要先燒熟）
const RAW_FOOD_ITEMS = new Set([
    'beef', 'porkchop', 'chicken', 'mutton', 'rabbit',
    'cod', 'salmon', 'potato',
])

// Minecraft 可食用物品
const FOOD_ITEMS = new Set([
    'bread', 'apple', 'golden_apple', 'enchanted_golden_apple',
    'cooked_beef', 'beef', 'cooked_chicken', 'chicken',
    'cooked_porkchop', 'porkchop', 'cooked_mutton', 'mutton',
    'cooked_rabbit', 'rabbit', 'cooked_cod', 'cod',
    'cooked_salmon', 'salmon', 'tropical_fish',
    'carrot', 'potato', 'baked_potato', 'beetroot',
    'melon_slice', 'pumpkin_pie', 'cookie',
    'mushroom_stew', 'rabbit_stew', 'suspicious_stew',
    'rotten_flesh',
])

let _isEating = false
let _lastEatTime = 0
let _lastFoodLowTime = 0
const EAT_COOLDOWN = 4000  // 吃完後 4 秒內不再觸發

function _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms))
}

function _waitForFoodIncrease(bot, beforeFood, timeoutMs = 2000) {
    return new Promise((resolve) => {
        if (bot.food > beforeFood) return resolve(true)

        const onHealth = () => {
            if (bot.food > beforeFood) cleanup(true)
        }
        const timer = setTimeout(() => cleanup(false), timeoutMs)

        function cleanup(result) {
            clearTimeout(timer)
            bot.removeListener('health', onHealth)
            resolve(result)
        }

        bot.on('health', onHealth)
    })
}

async function _tryEat(bot) {
    if (_isEating) return
    if (Date.now() - _lastEatTime < EAT_COOLDOWN) return
    if (bot.food >= 20) return

    const activity = getActivity()
    // 在戰鬥、狩獵、挖礦等高度依賴 path goal 的活動中，非緊急先不要插隊吃，
    // 否則 setGoal(null) 會一直打斷主行為。
    if (activity !== 'idle' && EAT_DEFER_ACTIVITIES.has(activity) && bot.food > FOOD_LOW_THRESHOLD) {
        return
    }

    const food = bot.inventory.items().find(i => FOOD_ITEMS.has(i.name))
    if (!food) {
        console.log('[Eat] 沒有食物可以吃')
        return
    }

    _isEating = true
    const prevItem = bot.heldItem
    const beforeFood = bot.food
    try {
        // 移動或挖掘會中斷 Mineflayer 的進食，先停下來再吃。
        bot.pathfinder?.setGoal(null)
        bot.clearControlStates()
        await _sleep(150)

        await bot.equip(food, 'hand')
        await bot.consume()
        const increased = await _waitForFoodIncrease(bot, beforeFood)
        if (!increased) {
            throw new Error(`食物沒有上升（${beforeFood} -> ${bot.food}），可能被其他動作打斷`)
        }
        _lastEatTime = Date.now()
        console.log(`[Eat] 吃了 ${food.name}，食物：${beforeFood} -> ${bot.food}/20`)

        // 吃完後恢復原本手持物品（如釣竿）
        if (prevItem && prevItem.name !== food.name) {
            const prev = bot.inventory.items().find(i => i.name === prevItem.name)
            if (prev) await bot.equip(prev, 'hand')
        }
    } catch (e) {
        console.log(`[Eat] 吃東西失敗: ${e.message}`)
    } finally {
        _isEating = false
    }
}

function _checkFoodLow(bot) {
    if (bot.food >= FOOD_LOW_THRESHOLD) return
    if (getActivity() !== 'idle') return
    if (Date.now() - _lastFoodLowTime < FOOD_LOW_COOLDOWN) return
    const hasFood = bot.inventory.items().some(i => FOOD_ITEMS.has(i.name))
    if (hasFood) return
    _lastFoodLowTime = Date.now()
    console.log(`[Eat] 食物不足（${bot.food}/20），背包無食物 → food_low`)
    bridge.sendState(bot, 'food_low')
}

function startMonitor(bot) {
    const check = () => {
        if (bot.food < FOOD_THRESHOLD) {
            _tryEat(bot).catch(e => console.log(`[Eat] 自動進食失敗: ${e.message}`))
        }
        _checkFoodLow(bot)
    }

    bot.on('health', check)
    // 撿到物品時也檢查（玩家丟食物給 bot）
    bot.on('playerCollect', (collector) => {
        if (collector.username === bot.username) check()
    })

    // 上線時立即檢查一次
    check()
    console.log('[Eat] 飢餓監控已啟動')
}

function isEating() {
    return _isEating
}

module.exports = { startMonitor, isEating, RAW_FOOD_ITEMS }
