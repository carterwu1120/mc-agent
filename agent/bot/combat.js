const execution = require('./execution')
const { goals } = require('mineflayer-pathfinder')
const activityStack = require('./activity')
const bridge = require('./bridge')

const FOOD_ANIMALS = new Set(['cow', 'pig', 'chicken', 'sheep', 'rabbit'])

const HOSTILE_MOBS = new Set([
    'zombie', 'skeleton', 'creeper', 'spider', 'cave_spider', 'witch',
    'slime', 'magma_cube', 'blaze', 'wither_skeleton',
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

let isCombating  = false
let _isPaused    = false
let _loopGen     = 0

let _lastWeaponCraftAttemptAt = 0
const WEAPON_CRAFT_COOLDOWN = 30000

activityStack.register('combat', _pause)

function _pause(_bot) {
    _loopGen++
    isCombating = false
    _isPaused = true
    console.log('[Combat] 暫停戰鬥')
}

function _armorTier(name) {
    for (const [mat, tier] of Object.entries(ARMOR_TIERS)) {
        if (name.startsWith(mat + '_')) return tier
    }
    return 0
}

function _isNearlyBroken(item) {
    if (!item || !item.maxDurability) return false
    return (item.maxDurability - item.durabilityUsed) < item.maxDurability * 0.1
}

async function equipArmor(bot) {
    for (const { slot, dest, suffix } of ARMOR_SLOTS) {
        const current = bot.inventory.slots[slot]
        const currentTier = (current && !_isNearlyBroken(current)) ? _armorTier(current.name) : 0
        const best = bot.inventory.items()
            .filter(i => i.name.endsWith(suffix))
            .sort((a, b) => _armorTier(b.name) - _armorTier(a.name))[0]
        if (best && _armorTier(best.name) > currentTier) {
            try {
                await bot.equip(best, dest)
                if (!execution.isCurrent()) return
                console.log(`[Combat] 裝備 ${best.name}`)
            } catch (e) {
                console.log(`[Combat] 裝備失敗 ${best.name}: ${e.message}`)
            }
        }
    }
}

// Ingots/diamonds per piece; the same for iron and diamond armor.
const ARMOR_COST = { '_helmet': 5, '_chestplate': 8, '_leggings': 7, '_boots': 4 }
const ARMOR_MATERIAL_ITEM = { iron: 'iron_ingot', diamond: 'diamond' }

// Crafts and equips <material> armor for every slot that is empty, nearly broken or below
// that tier, using only carried materials. Never gathers or smelts: the planner decides
// whether armor is worth it and supplies the materials. Returns what it made and what is
// still missing.
async function makeArmor(bot, material) {
    const item = ARMOR_MATERIAL_ITEM[material]
    if (!item) throw new Error('invalid_material')
    const wantTier = ARMOR_TIERS[material]
    const count = () => bot.inventory.items().filter(i => i.name === item).reduce((n, i) => n + i.count, 0)
    const needed = ARMOR_SLOTS.filter(({ slot }) => {
        const cur = bot.inventory.slots[slot]
        return !cur || _isNearlyBroken(cur) || _armorTier(cur.name) < wantTier
    })
    const crafted = []
    if (needed.length && count() >= Math.min(...needed.map(({ suffix }) => ARMOR_COST[suffix]))) {
        const { ensureCraftingTable, reclaimCraftingTable } = require('./crafting')
        const table = await ensureCraftingTable(bot)
        if (!execution.isCurrent()) return { crafted, missing: 0 }
        if (table) {
            try {
                for (const { dest, suffix } of needed) {
                    if (count() < ARMOR_COST[suffix]) continue
                    const pieceName = `${material}${suffix}`
                    const recipe = bot.recipesFor(bot.registry.itemsByName[pieceName].id, null, 1, table)[0]
                    if (!recipe) continue
                    try {
                        await bot.craft(recipe, 1, table)
                        if (!execution.isCurrent()) return { crafted, missing: 0 }
                        const piece = bot.inventory.items().find(i => i.name === pieceName)
                        if (piece) await bot.equip(piece, dest)
                        crafted.push(pieceName)
                        console.log(`[Armor] 合成並裝備 ${pieceName}`)
                    } catch (e) {
                        console.log(`[Armor] 合成 ${pieceName} 失敗: ${e.message}`)
                    }
                }
            } finally {
                await reclaimCraftingTable(bot)
            }
        }
    }
    const remaining = needed
        .filter(({ suffix }) => !crafted.includes(`${material}${suffix}`))
        .reduce((n, { suffix }) => n + ARMOR_COST[suffix], 0)
    return { crafted, missing: Math.max(0, remaining - count()) }
}

async function equipWeapon(bot) {
    const SWORDS = WEAPON_PRIORITY.filter(n => n.endsWith('_sword'))
    const AXES   = WEAPON_PRIORITY.filter(n => n.endsWith('_axe') || n === 'trident')

    const _tryEquip = async (name) => {
        const item = bot.inventory.items().find(i => i.name === name)
        if (!item) return false
        try { await bot.equip(item, 'hand'); console.log(`[Combat] 武器 ${item.name}`); return true } catch (_) { return false }
    }

    // 1. 找劍
    for (const name of SWORDS) { if (await _tryEquip(name)) return true }

    // 2. 沒劍 → 嘗試合成
    let crafted = false
    if (Date.now() - _lastWeaponCraftAttemptAt >= WEAPON_CRAFT_COOLDOWN) {
        _lastWeaponCraftAttemptAt = Date.now()
        console.log('[Combat] 背包無劍，嘗試合成（30 秒內最多一次）...')
        const { ensureSword } = require('./crafting')
        crafted = await ensureSword(bot)
        if (!execution.isCurrent()) return
    }
    if (crafted) {
        for (const name of SWORDS) { if (await _tryEquip(name)) return true }
    }

    // 3. 合成失敗 → 找斧頭
    for (const name of AXES) { if (await _tryEquip(name)) return true }

    // 4. 什麼都沒有 → 空手
    try { await bot.unequip('hand') } catch (_) {}
    return false
}

async function startCombat(bot, goal = {}) {
    if (isCombating) return

    isCombating = true
    activityStack.push(bot, 'combat', goal, (b) => _resumeCombat(b, goal))
    console.log(`[Combat] 開始戰鬥 goal=${JSON.stringify(goal)}`)
    _loop(bot, goal)
}

function _resumeCombat(bot, originalGoal) {
    if (isCombating) return
    isCombating = true
    activityStack.updateTopGoal(originalGoal)
    console.log('[Combat] 恢復戰鬥')
    _loop(bot, originalGoal)
}

function stopCombat(_bot) {
    if (!isCombating) return
    isCombating = false
    _isPaused = false
    _loopGen++
    console.log('[Combat] 停止戰鬥')
}

function isActive() {
    return isCombating
}

// Self-defense (started by the health monitor) only fights near where it was attacked,
// then hands control back to the interrupted task. It must not chase mobs through caves
// or report the task as stuck when the attacker gets away.
const DEFENSIVE_LEASH_HORIZONTAL = 8
const DEFENSIVE_LEASH_VERTICAL = 4

function _outsideLeash(origin, pos, horizontal = DEFENSIVE_LEASH_HORIZONTAL) {
    return Math.hypot(pos.x - origin.x, pos.z - origin.z) > horizontal ||
        Math.abs(pos.y - origin.y) > DEFENSIVE_LEASH_VERTICAL
}

// Ranged attackers keep hitting from outside the melee leash. Ignoring them meant
// standing still and being shot to death (twice in a row after a respawn).
const RANGED_MOBS = new Set(['skeleton', 'stray', 'bogged', 'pillager', 'blaze', 'witch', 'drowned'])
const RANGED_CHASE_HORIZONTAL = 24
const MIN_HEALTH_TO_CHASE = 8

function _canFightBack(bot) {
    const weapon = bot.inventory.items().some(i => WEAPON_PRIORITY.includes(i.name))
    return weapon && bot.health >= MIN_HEALTH_TO_CHASE
}

// Move out of an archer's range and line of sight for a few seconds.
async function _retreatFrom(bot, target) {
    console.log(`[Combat] 無法反擊遠程的 ${target.name}，撤離射程`)
    try {
        await Promise.race([
            bot.pathfinder.goto(new goals.GoalInvert(new goals.GoalFollow(target, 20))),
            _sleep(8000),
        ])
    } catch (_) {}
    try { bot.pathfinder.setGoal(null) } catch (_) {}
}

async function _loop(bot, goal = {}) {
    _isPaused = false
    const _myGen = ++_loopGen
    const startTime = Date.now()
    const defensive = goal.defensive === true
    const origin = bot.entity.position.clone()
    let noTargetTicks = 0
    let gotoFailStreak = 0
    const MAX_GOTO_FAILS = 5

    while (isCombating && execution.isCurrent() && _loopGen === _myGen) {
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Combat] 達到時間目標 ${goal.duration}s，停止`)
            break
        }

        const target = _findTarget(bot, goal.target)
        if (!target || !target.isValid) {
            noTargetTicks++
            if (noTargetTicks >= 4) {
                console.log('[Combat] 附近無敵對生物，結束戰鬥')
                break
            }
            await _sleep(500)
            if (!execution.isCurrent()) return
            continue
        }
        if (defensive && _outsideLeash(origin, target.position)) {
            const ranged = RANGED_MOBS.has(target.name)
            if (!ranged) {
                console.log(`[Combat] ${target.name} 已離開自衛範圍，結束戰鬥並回到原任務`)
                break
            }
            if (!_canFightBack(bot) || _outsideLeash(origin, target.position, RANGED_CHASE_HORIZONTAL)) {
                await _retreatFrom(bot, target)
                break
            }
            // An archer within reach: close in on it rather than stand in its line of fire.
        }
        activityStack.touch('combat', 'target_acquired')
        noTargetTicks = 0

        // 若腳下或身體位置有蜘蛛網，先挖掉脫身
        await _clearCobwebs(bot)
        if (!execution.isCurrent()) return

        // 每次攻擊前確認手持武器（其他模組可能切換了手上物品）
        const handItem = bot.heldItem
        if (!handItem || !WEAPON_PRIORITY.includes(handItem.name)) {
            await equipWeapon(bot)
            if (!execution.isCurrent()) return
        }

        const dist = target.position.distanceTo(bot.entity.position)
        if (dist > 3) {
            let reached = false
            try {
                await Promise.race([
                    bot.pathfinder.goto(new goals.GoalNear(target.position.x, target.position.y, target.position.z, 2)).then(() => { reached = true }),
                    _sleep(5000).then(() => { bot.pathfinder.setGoal(null) }),
                ])
            } catch (_) {}
            if (reached) {
                gotoFailStreak = 0
                activityStack.touch('combat', 'goto_target')
            } else {
                gotoFailStreak++
                if (gotoFailStreak >= MAX_GOTO_FAILS) {
                    console.log(`[Combat] 連續 ${gotoFailStreak} 次無法抵達目標，放棄戰鬥`)
                    // Giving up self-defense is not a task failure; the parent activity resumes.
                    if (defensive) break
                    isCombating = false
                    bridge.sendState(bot, 'activity_stuck', { reason: 'cannot_reach_target', detail: `failed to reach ${target.name} after ${gotoFailStreak} attempts` })
                    break
                }
            }
        } else {
            gotoFailStreak = 0
        }

        if (!isCombating) break

        try {
            await bot.lookAt(target.position.offset(0, (target.height ?? 1.8) / 2, 0))
            if (!execution.isCurrent()) return
            bot.attack(target)
            activityStack.touch('combat', 'attack')
        } catch (e) {
            console.log(`[Combat] 攻擊失敗: ${e.message}`)
        }

        await _sleep(600)
        if (!execution.isCurrent()) return
    }

    if (!execution.isCurrent() || _loopGen !== _myGen) return
    isCombating = false
    if (!_isPaused && _loopGen === _myGen) {
        // Only re-equip what is carried; crafting armor is the planner's decision (makearmor).
        execution.run(() => setTimeout(() => equipArmor(bot), 1000), execution.commandId())
        activityStack.pop(bot)
    }
    _isPaused = false
}

function _findTarget(bot, preferType) {
    const selfPos = bot.entity.position
    return Object.values(bot.entities)
        .filter(e => {
            if (e.id === bot.entity.id) return false
            if (!e.isValid || !e.position) return false
            if (e.position.distanceTo(selfPos) > 16) return false
            const name = (e.name || e.mobType || '').toLowerCase()
            if (preferType === 'animal') return FOOD_ANIMALS.has(name)
            if (preferType) return name.includes(preferType.toLowerCase())
            return HOSTILE_MOBS.has(name)
        })
        .sort((a, b) => a.position.distanceTo(selfPos) - b.position.distanceTo(selfPos))[0]
}

async function _clearCobwebs(bot) {
    const feet = bot.entity.position.floored()
    for (const dy of [0, 1]) {
        const b = bot.blockAt(feet.offset(0, dy, 0))
        if (b && b.name === 'cobweb') {
            try {
                const tool = bot.pathfinder.bestHarvestTool(b)
                if (tool) await bot.equip(tool, 'hand')
                await bot.dig(b)
                if (!execution.isCurrent()) return
                console.log('[Combat] 清除蜘蛛網')
            } catch (_) {}
        }
    }
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
                startCombat(bot, { defensive: true })
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

module.exports = { startCombat, stopCombat, isActive, equipArmor, equipWeapon, makeArmor, startMonitor }

// Each activity has its own cancellable async scope, including nested activities.
for (const [name, fn] of Object.entries(module.exports)) {
    if (name.startsWith('start') && name !== 'startMonitor') module.exports[name] = execution.owned(fn)
}
