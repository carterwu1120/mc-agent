const { goals } = require('mineflayer-pathfinder')
const activityStack = require('./activity')
const bridge = require('./bridge')

const FOOD_ANIMALS = new Set(['cow', 'pig', 'chicken', 'sheep', 'rabbit'])
const SEARCH_RADIUS = 48
const DROPS_WAIT = 1500  // ms to wait after kill for drops to appear
const SWORD_PRIORITY = [
    'netherite_sword', 'diamond_sword', 'iron_sword', 'stone_sword',
    'golden_sword', 'wooden_sword',
]

let isHunting = false
let _isPaused = false
let _killCount = 0

activityStack.register('hunting', _pause)

function _pause(_bot) {
    isHunting = false
    _isPaused = true
    console.log('[Hunt] 暫停狩獵')
}

async function startHunting(bot, goal = {}) {
    if (isHunting) return
    isHunting = true
    _killCount = 0
    activityStack.push(bot, 'hunting', goal, (b) => _resumeHunting(b, goal))
    _loop(bot, goal)
}

function _resumeHunting(bot, originalGoal) {
    if (isHunting) return
    const remaining = originalGoal.count
        ? Math.max(1, originalGoal.count - _killCount) : undefined
    isHunting = true
    activityStack.updateTopGoal(remaining ? { ...originalGoal, count: remaining } : originalGoal)
    _loop(bot, originalGoal)
}

function stopHunting(_bot) {
    if (!isHunting) return
    isHunting = false
    _isPaused = false
}

async function _loop(bot, goal) {
    _isPaused = false
    const maxCount = goal.count ?? 3
    let noAnimalTicks = 0

    try {
        await _equipSword(bot)
    } catch (_) {}

    while (isHunting) {
        if (_killCount >= maxCount) {
            console.log(`[Hunt] 已獵殺 ${_killCount} 隻，完成`)
            isHunting = false
            bridge.sendState(bot, 'activity_done', { activity: 'hunting', goal })
            break
        }

        const animal = _findAnimal(bot)
        if (!animal) {
            noAnimalTicks++
            if (noAnimalTicks >= 6) {
                console.log('[Hunt] 找不到食用動物，停止')
                isHunting = false
                bridge.sendState(bot, 'activity_done', { activity: 'hunting', goal })
                break
            }
            await _sleep(2000)
            continue
        }
        noAnimalTicks = 0

        console.log(`[Hunt] 目標：${animal.name}（進度 ${_killCount}/${maxCount}）`)

        // 移動靠近
        if (animal.position.distanceTo(bot.entity.position) > 3) {
            try {
                await Promise.race([
                    bot.pathfinder.goto(
                        new goals.GoalNear(animal.position.x, animal.position.y, animal.position.z, 2)
                    ),
                    _sleep(10000).then(() => { bot.pathfinder.setGoal(null) }),
                ])
            } catch (_) {}
        }

        if (!isHunting) break

        // 攻擊直到死亡
        const killed = await _killAnimal(bot, animal)
        if (killed) {
            _killCount++
            activityStack.updateProgress({ count: _killCount })
            console.log(`[Hunt] 獵殺成功，總計 ${_killCount}/${maxCount}`)
            await _sleep(DROPS_WAIT)  // 等掉落物出現
            await _collectNearbyDrops(bot, animal.position, 6)
        }
    }

    if (!_isPaused) activityStack.pop(bot)
    _isPaused = false
}

async function _killAnimal(bot, animal) {
    const maxAttempts = 40
    for (let i = 0; i < maxAttempts && isHunting; i++) {
        if (!animal.isValid) return true  // 已死亡

        const handItem = bot.heldItem
        if (!handItem || !SWORD_PRIORITY.includes(handItem.name)) {
            await _equipSword(bot)
        }

        const dist = animal.position.distanceTo(bot.entity.position)
        if (dist > 3) {
            try {
                await Promise.race([
                    bot.pathfinder.goto(
                        new goals.GoalNear(animal.position.x, animal.position.y, animal.position.z, 2)
                    ),
                    _sleep(4000).then(() => { bot.pathfinder.setGoal(null) }),
                ])
            } catch (_) {}
            if (!animal.isValid) return true
        }

        try {
            await bot.lookAt(animal.position.offset(0, (animal.height ?? 1.6) / 2, 0))
            bot.attack(animal)
        } catch (_) {}
        await _sleep(600)
    }
    return !animal.isValid
}

async function _equipSword(bot) {
    for (const name of SWORD_PRIORITY) {
        const sword = bot.inventory.items().find(i => i.name === name)
        if (!sword) continue
        try {
            await bot.equip(sword, 'hand')
            console.log(`[Hunt] 裝備 ${sword.name}`)
            return true
        } catch (_) {}
    }

    const { ensureSword } = require('./crafting')
    const crafted = await ensureSword(bot)
    if (crafted) {
        for (const name of SWORD_PRIORITY) {
            const sword = bot.inventory.items().find(i => i.name === name)
            if (!sword) continue
            try {
                await bot.equip(sword, 'hand')
                console.log(`[Hunt] 裝備 ${sword.name}`)
                return true
            } catch (_) {}
        }
    }

    console.log('[Hunt] 沒有可用劍，維持目前手持物')
    return false
}

function _findAnimal(bot) {
    const self = bot.entity.position
    return Object.values(bot.entities)
        .filter(e =>
            e.isValid &&
            e.position &&
            FOOD_ANIMALS.has((e.name || '').toLowerCase()) &&
            e.position.distanceTo(self) <= SEARCH_RADIUS
        )
        .sort((a, b) => a.position.distanceTo(self) - b.position.distanceTo(self))[0] || null
}

async function _collectNearbyDrops(bot, nearPos, maxDistance) {
    const drops = Object.values(bot.entities)
        .filter(e =>
            e.name === 'item' &&
            e.position &&
            e.position.distanceTo(nearPos) <= maxDistance
        )
        .sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))

    for (const drop of drops) {
        if (!isHunting) return
        try {
            await bot.pathfinder.goto(
                new goals.GoalNear(drop.position.x, drop.position.y, drop.position.z, 1)
            )
            await _sleep(250)
        } catch (_) {}
    }
}

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

module.exports = { startHunting, stopHunting, isActive: () => isHunting }
