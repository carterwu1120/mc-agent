const HEALTH_THRESHOLD = 20  // 血量低於此值就吃東西（補血）

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
const EAT_COOLDOWN = 4000  // 吃完後 4 秒內不再觸發

async function _tryEat(bot) {
    if (_isEating) return
    if (Date.now() - _lastEatTime < EAT_COOLDOWN) return

    const food = bot.inventory.items().find(i => FOOD_ITEMS.has(i.name))
    if (!food) {
        console.log('[Eat] 沒有食物可以吃')
        return
    }

    _isEating = true
    const prevItem = bot.heldItem
    try {
        await bot.equip(food, 'hand')
        await bot.consume()
        _lastEatTime = Date.now()
        console.log(`[Eat] 吃了 ${food.name}，食物：${bot.food}/20`)

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

function startMonitor(bot) {
    const check = () => {
        if (bot.health < HEALTH_THRESHOLD && bot.food < 20) {
            _tryEat(bot)
        }
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

module.exports = { startMonitor }
