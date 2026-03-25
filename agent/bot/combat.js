const { goals } = require('mineflayer-pathfinder')
const { setActivity } = require('./activity')
const bridge = require('./bridge')

const HOSTILE_MOBS = new Set([
    'zombie', 'skeleton', 'creeper', 'spider', 'cave_spider', 'witch',
    'enderman', 'slime', 'magma_cube', 'blaze', 'wither_skeleton',
    'zombie_villager', 'husk', 'stray', 'drowned', 'phantom',
    'pillager', 'vindicator', 'evoker', 'vex', 'ravager',
    'endermite', 'silverfish', 'guardian', 'elder_guardian',
])

const WEAPON_PRIORITY = [
    'netherite_sword', 'diamond_sword', 'iron_sword', 'stone_sword',
    'golden_sword', 'wooden_sword',
    'netherite_axe', 'diamond_axe', 'iron_axe', 'stone_axe', 'wooden_axe',
    'trident',
]

const ARMOR_TIERS = { netherite: 5, diamond: 4, iron: 3, chainmail: 2, golden: 2, leather: 1 }
const ARMOR_SLOTS = [
    { slot: 5, dest: 'head',  suffix: '_helmet' },
    { slot: 6, dest: 'torso', suffix: '_chestplate' },
    { slot: 7, dest: 'legs',  suffix: '_leggings' },
    { slot: 8, dest: 'feet',  suffix: '_boots' },
]

let isCombating = false
let _wasFishing = false
let _wasChopping = false
let _wasMining = false
let _wasSmelting = false
let _savedMiningGoal = {}

function _armorTier(name) {
    for (const [mat, tier] of Object.entries(ARMOR_TIERS)) {
        if (name.startsWith(mat + '_')) return tier
    }
    return 0
}

async function equipArmor(bot) {
    for (const { slot, dest, suffix } of ARMOR_SLOTS) {
        const current = bot.inventory.slots[slot]
        const currentTier = current ? _armorTier(current.name) : 0
        const best = bot.inventory.items()
            .filter(i => i.name.endsWith(suffix))
            .sort((a, b) => _armorTier(b.name) - _armorTier(a.name))[0]
        if (best && _armorTier(best.name) > currentTier) {
            try {
                await bot.equip(best, dest)
                console.log(`[Combat] 裝備 ${best.name}`)
            } catch (e) {
                console.log(`[Combat] 裝備失敗 ${best.name}: ${e.message}`)
            }
        }
    }
}

async function equipWeapon(bot) {
    for (const name of WEAPON_PRIORITY) {
        const item = bot.inventory.items().find(i => i.name === name)
        if (item) {
            try {
                await bot.equip(item, 'hand')
                console.log(`[Combat] 武器 ${item.name}`)
                return true
            } catch (e) {
                console.log(`[Combat] 武器裝備失敗 ${item.name}: ${e.message}`)
            }
        }
    }
    return false
}

async function startCombat(bot, goal = {}) {
    if (isCombating) return

    const { isActive: isFishing, stopFishing, startFishing } = require('./fishing')
    const { isActive: isChopping, stopChopping, startChopping } = require('./woodcutting')
    const { isActive: isMining, stopMining, startMining, getGoal: getMiningGoal } = require('./mining')
    const { isActive: isSmelting, stopSmelting, startSmelting } = require('./smelting')

    _wasFishing = isFishing()
    _wasChopping = isChopping()
    _wasMining = isMining()
    _wasSmelting = isSmelting()
    if (_wasFishing) stopFishing(bot)
    if (_wasChopping) stopChopping(bot)
    if (_wasMining) { _savedMiningGoal = getMiningGoal(); stopMining(bot) }
    if (_wasSmelting) stopSmelting(bot)

    isCombating = true
    setActivity('combat')
    console.log(`[Combat] 開始戰鬥 goal=${JSON.stringify(goal)}`)
    _loop(bot, goal)
}

function stopCombat(bot) {
    if (!isCombating) return
    isCombating = false
    setActivity('idle')
    console.log('[Combat] 停止戰鬥')

    const { startFishing } = require('./fishing')
    const { startChopping } = require('./woodcutting')
    const { startMining } = require('./mining')
    const { startSmelting } = require('./smelting')

    if (_wasFishing) { console.log('[Combat] 恢復釣魚'); startFishing(bot) }
    if (_wasChopping) { console.log('[Combat] 恢復砍樹'); startChopping(bot) }
    if (_wasMining) { console.log('[Combat] 恢復挖礦'); startMining(bot, _savedMiningGoal) }
    if (_wasSmelting) { console.log('[Combat] 恢復燒製'); startSmelting(bot) }

    _wasFishing = false
    _wasChopping = false
    _wasMining = false
    _wasSmelting = false
    _savedMiningGoal = {}
}

function isActive() {
    return isCombating
}

async function _loop(bot, goal = {}) {
    const startTime = Date.now()
    let weaponEquipped = false

    while (isCombating) {
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Combat] 達到時間目標 ${goal.duration}s，停止`)
            isCombating = false
            setActivity('idle')
            bridge.sendState(bot, 'activity_done', { activity: 'combat', reason: 'goal_reached' })
            break
        }

        if (!weaponEquipped) {
            await equipWeapon(bot)
            weaponEquipped = true
        }

        const target = _findTarget(bot, goal.target)
        if (!target || !target.isValid) {
            await _sleep(500)
            continue
        }

        const dist = target.position.distanceTo(bot.entity.position)
        if (dist > 3) {
            try {
                await bot.pathfinder.goto(
                    new goals.GoalNear(target.position.x, target.position.y, target.position.z, 2)
                )
            } catch (_) {}
        }

        if (!isCombating) break

        try {
            await bot.lookAt(target.position.offset(0, (target.height ?? 1.8) / 2, 0))
            bot.attack(target)
        } catch (e) {
            console.log(`[Combat] 攻擊失敗: ${e.message}`)
        }

        await _sleep(600)
    }
}

function _findTarget(bot, preferType) {
    const selfPos = bot.entity.position
    return Object.values(bot.entities)
        .filter(e => {
            if (e.id === bot.entity.id) return false
            if (!e.isValid || !e.position) return false
            if (e.position.distanceTo(selfPos) > 16) return false
            const name = e.name || e.mobType || ''
            if (preferType) return name.toLowerCase().includes(preferType.toLowerCase())
            return HOSTILE_MOBS.has(name.toLowerCase())
        })
        .sort((a, b) => a.position.distanceTo(selfPos) - b.position.distanceTo(selfPos))[0]
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

function startMonitor(bot) {
    let _lastHealth = null

    bot.on('health', () => {
        if (_lastHealth === null) { _lastHealth = bot.health; return }
        if (bot.health < _lastHealth - 0.5 && !isCombating) {
            const target = _findTarget(bot)
            if (target) {
                console.log(`[Combat] 受到攻擊！血量 ${_lastHealth} → ${bot.health}，反擊 ${target.name}`)
                startCombat(bot)
            } else {
                console.log(`[Combat] 血量下降 ${_lastHealth} → ${bot.health}，附近無敵對生物（環境傷害），忽略`)
            }
        }
        _lastHealth = bot.health
    })

    bot.on('playerCollect', (collector) => {
        if (collector.username !== bot.username) return
        setTimeout(() => equipArmor(bot), 500)
    })

    console.log('[Combat] 戰鬥監控已啟動')
}

module.exports = { startCombat, stopCombat, isActive, equipArmor, equipWeapon, startMonitor }
