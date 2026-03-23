const { goals, Movements } = require('mineflayer-pathfinder')
const { setActivity } = require('./activity')
const { ensureToolFor, ensurePickaxeTier } = require('./crafting')
const bridge = require('./bridge')
const { isBuried } = require('./buried')

let isMining = false
let _currentGoal = {}

const ORE_PRIORITY = [
    'diamond', 'emerald', 'ancient_debris',
    'gold', 'iron', 'copper',
    'lapis', 'redstone', 'coal',
]


const ORE_BEST_Y = {
    coal: 96, iron: 16, copper: 48,
    lapis: 0, gold: -16, redstone: -16,
    diamond: -58, emerald: 232,
}

// 挖各礦石所需最低稿子等級
const ORE_MIN_PICKAXE = {
    diamond:        'iron_pickaxe',
    emerald:        'iron_pickaxe',
    ancient_debris: 'diamond_pickaxe',
    gold:           'iron_pickaxe',
    iron:           'stone_pickaxe',
    // coal, copper, lapis, redstone, stone → 木稿即可，不需特別限制
}

function _requiredPickaxe(blockName) {
    for (const [ore, minPick] of Object.entries(ORE_MIN_PICKAXE)) {
        if (blockName.includes(ore)) return minPick
    }
    return 'wooden_pickaxe'
}


function _isExposed(bot, pos) {
    const offsets = [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]]
    return offsets.some(([dx, dy, dz]) => {
        const b = bot.blockAt(pos.offset(dx, dy, dz))
        return b && (b.name === 'air' || b.name === 'cave_air')
    })
}

function _priority(name) {
    if (!name) return 999
    const idx = ORE_PRIORITY.findIndex(o => name.includes(o))
    return idx === -1 ? 100 : idx
}

function _setMovements(bot) {
    const movements = new Movements(bot)
    movements.canDig = true
    bot.pathfinder.setMovements(movements)
}

async function startMining(bot, goal = {}) {
    if (isMining) {
        console.log('[Mine] 已在挖礦中')
        return
    }
    if (goal.count !== undefined && !Number.isFinite(goal.count)) delete goal.count
    _currentGoal = goal
    const { isActive: isSmeltingActive, stopSmelting } = require('./smelting')
    if (isSmeltingActive()) stopSmelting(bot)
    isMining = true
    setActivity('mining')
    console.log('[Mine] 開始挖礦')
    _loop(bot, goal)
}

function stopMining(bot) {
    if (!isMining) return
    isMining = false
    setActivity('idle')
    console.log('[Mine] 停止挖礦')
}

async function _loop(bot, goal = {}) {
    const startTime = Date.now()
    let targetCount = 0
    let tunnelYaw = bot.entity.yaw
    const bestY = goal.target ? (ORE_BEST_Y[goal.target] ?? null) : null
    let lastDescentY = null
    let stuckCount = 0
    let tunnelFailCount = 0
    const unavailablePickaxe = new Set()  // 本輪無法取得的稿子等級，跳過需要它的礦

    while (isMining) {
        // 每輪檢查：若之前因材料不足跳過某等級，看背包現在是否已有足夠材料可解除
        if (unavailablePickaxe.size > 0) {
            const ironIngots = bot.inventory.items().filter(i => i.name === 'iron_ingot').reduce((s, i) => s + i.count, 0)
            const rawIron    = bot.inventory.items().some(i => ['raw_iron','iron_ore','deepslate_iron_ore'].includes(i.name))
            if (ironIngots >= 3 || rawIron) {
                console.log('[Mine] 已有足夠鐵礦/鐵錠，解除稿子限制')
                unavailablePickaxe.clear()
            }
        }

        // 停止條件
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Mine] 達到時間目標 ${goal.duration}s，停止`)
            isMining = false
            setActivity('idle')
            bridge.sendState(bot, 'activity_done', { activity: 'mining', reason: 'goal_reached' })
            break
        }
        if (goal.target && goal.count && targetCount >= goal.count) {
            console.log(`[Mine] 達到目標 ${goal.target} x${goal.count}，停止`)
            isMining = false
            setActivity('idle')
            bridge.sendState(bot, 'activity_done', { activity: 'mining', reason: 'goal_reached' })
            break
        }

        const currentY = Math.floor(bot.entity.position.y)
        const needDescend = bestY !== null && currentY - bestY > 5
        const needAscend  = bestY !== null && bestY - currentY > 5

        if (needAscend) {
            console.log(`[Mine] 位置過深 Y=${currentY}，回到目標高度 Y=${bestY}`)
            _setMovements(bot)
            try {
                await bot.pathfinder.goto(new goals.GoalNear(
                    Math.floor(bot.entity.position.x), bestY,
                    Math.floor(bot.entity.position.z), 3
                ))
            } catch (e) {
                console.log('[Mine] 無法上升到目標高度:', e.message)
            }
        } else if (needDescend) {
            // 主動作：挖階梯往下
            await _stepDown(bot, bestY, tunnelYaw)
            if (!isMining) return

            // 偵測卡住：Y 沒有下降就累計，超過 3 次換方向
            const afterY = Math.floor(bot.entity.position.y)
            if (lastDescentY !== null && afterY >= lastDescentY) {
                stuckCount++
                if (stuckCount >= 3) {
                    tunnelYaw += Math.PI / 2
                    stuckCount = 0
                    console.log('[Mine] 下潛方向受阻，旋轉 90° 繼續')
                }
            } else {
                stuckCount = 0
            }
            lastDescentY = afterY

            // 順手：挖階梯時旁邊看到的礦（8 格內）
            const nearbyOres = bot.findBlocks({ matching: b => b.name.endsWith('_ore'), maxDistance: 8, count: 20 })
                .filter(p => _isExposed(bot, p))
                .sort((a, b) => _priority(bot.blockAt(a)?.name) - _priority(bot.blockAt(b)?.name))

            for (const orePos of nearbyOres) {
                if (!isMining) return
                const block = bot.blockAt(orePos)
                if (!block || !block.name.endsWith('_ore')) continue

                _setMovements(bot)
                try {
                    await bot.pathfinder.goto(new goals.GoalNear(orePos.x, orePos.y, orePos.z, 2))
                    if (!isMining) return
                    const fresh = bot.blockAt(orePos)
                    if (!fresh || !fresh.name.endsWith('_ore')) continue
                    const required = _requiredPickaxe(fresh.name)
                    const ok = await ensurePickaxeTier(bot, required)
                    if (!ok) continue
                    await bot.dig(fresh)
                    const isTarget = goal.target && fresh.name.includes(goal.target)
                    if (isTarget) targetCount++
                    console.log(`[Mine] 挖下 ${fresh.name}${isTarget ? ` (目標 ${targetCount}/${goal.count})` : ''}`)
                    await _sleep(300)
                    await _collectNearby(bot, orePos, 4)
                } catch (_) {}
            }

        } else if (!needAscend) {
            // 已到目標深度：找附近礦石挖（嚴格限制在 bestY ±1），沒有就挖隧道
            const allExposed = bot.findBlocks({ matching: b => b.name.endsWith('_ore'), maxDistance: 16, count: 50 })
                .filter(p => {
                    if (!_isExposed(bot, p)) return false
                    if (bestY !== null && Math.abs(p.y - bestY) > 1) return false
                    if (unavailablePickaxe.size > 0) {
                        const req = _requiredPickaxe(bot.blockAt(p)?.name)
                        if (unavailablePickaxe.has(req)) return false
                    }
                    return true
                })
                .sort((a, b) =>
                    _priority(bot.blockAt(a)?.name) - _priority(bot.blockAt(b)?.name) ||
                    a.distanceTo(bot.entity.position) - b.distanceTo(bot.entity.position)
                )

            if (allExposed.length > 0) {
                const pos = allExposed[0]
                const block = bot.blockAt(pos)
                if (!block) continue

                console.log(`[Mine] 目標 ${block.name} at y=${pos.y}`)
                _setMovements(bot)
                try {
                    await bot.pathfinder.goto(new goals.GoalNear(pos.x, pos.y, pos.z, 2))
                } catch (e) { continue }

                if (!isMining) return

                const fresh = bot.blockAt(pos)
                if (!fresh || !fresh.name.endsWith('_ore')) continue

                try {
                    const required = _requiredPickaxe(fresh.name)
                    const ok = await ensurePickaxeTier(bot, required)
                    if (!ok) {
                        console.log(`[Mine] 材料不足無法取得 ${required}，跳過需要它的礦`)
                        unavailablePickaxe.add(required)
                        continue
                    }
                    unavailablePickaxe.delete(required)  // 成功取得，解除跳過
                    await bot.dig(fresh)
                    const isTarget = goal.target && fresh.name.includes(goal.target)
                    if (isTarget) targetCount++
                    console.log(`[Mine] 挖下 ${fresh.name}${isTarget ? ` (目標 ${targetCount}/${goal.count})` : ''}`)
                    await _sleep(300)
                    await _collectNearby(bot, pos, 4)
                } catch (e) {
                    console.log('[Mine] 挖掘失敗:', e.message)
                    await _sleep(300)
                }

            } else {
                console.log('[Mine] 附近沒有礦石，挖隧道繼續')
                const tunneled = await _digTunnel(bot, tunnelYaw, 8, bestY)
                if (!tunneled) {
                    tunnelFailCount++
                    tunnelYaw += Math.PI / 2
                    console.log(`[Mine] 隧道受阻，旋轉方向繼續 (${tunnelFailCount}/4)`)
                    if (tunnelFailCount >= 4) {
                        console.log('[Mine] 四個方向都無法繼續，嘗試往上逃脫...')
                        _setMovements(bot)
                        try {
                            await bot.pathfinder.goto(new goals.GoalNear(
                                Math.floor(bot.entity.position.x),
                                64,
                                Math.floor(bot.entity.position.z),
                                10
                            ))
                            console.log('[Mine] 已往上移動')
                        } catch (e) {
                            console.log('[Mine] 無法往上移動:', e.message)
                        }
                        isMining = false
                        setActivity('idle')
                        bridge.sendState(bot, 'activity_done', { activity: 'mining', reason: 'no_blocks' })
                        break
                    }
                } else {
                    tunnelFailCount = 0
                }
            }
        }
    }
}

// 往目標 Y 走一步（pathfinder 自動挖出階梯）
// 手動挖斜梯：每次「前進1格 + 往下1格」，重複 steps 次
async function _stairDown(bot, yaw, steps) {
    const dx = Math.round(-Math.sin(yaw))
    const dz = Math.round(-Math.cos(yaw))

    for (let i = 0; i < steps; i++) {
        if (!isMining) return

        const feet = bot.entity.position.floored()

        // 挖前方 2 格（腳 + 頭）
        await ensureToolFor(bot, 'stone')  // 確保稿子在手
        for (const off of [[dx, 0, dz], [dx, 1, dz]]) {
            const b = bot.blockAt(feet.offset(...off))
            if (b && b.boundingBox === 'block') {
                try { await bot.dig(b) } catch (_) {}
            }
        }

        // 走進前方格
        _setMovements(bot)
        try {
            await bot.pathfinder.goto(new goals.GoalBlock(feet.x + dx, feet.y, feet.z + dz))
        } catch (_) { break }

        await _sleep(100)

        // 挖腳下的格，往下掉一格
        await ensureToolFor(bot, 'stone')  // pathfinder 可能換了手持物品，重新裝備
        const newFeet = bot.entity.position.floored()
        const below = bot.blockAt(newFeet.offset(0, -1, 0))
        if (below && below.boundingBox === 'block') {
            try { await bot.dig(below) } catch (_) { break }
        }

        await _sleep(300)  // 等掉落
    }
}

async function _stepDown(bot, targetY, yaw) {
    const ok = await ensureToolFor(bot, 'stone')
    if (!ok) {
        console.log('[Mine] 沒有稿子，無法下潛')
        return
    }
    const currentY = Math.floor(bot.entity.position.y)
    const steps = Math.min(3, currentY - targetY)
    if (steps <= 0) return
    console.log(`[Mine] 下潛斜梯 ${steps} 格 → Y=${currentY - steps}`)
    await _stairDown(bot, yaw, steps)
}

// 挖 2×2 隧道往前，回傳是否有成功挖進去
async function _digTunnel(bot, yaw, length = 8, targetY = null) {
    let dug = false
    const dx = Math.round(-Math.sin(yaw))
    const dz = Math.round(-Math.cos(yaw))
    // 垂直於前進方向的側邊偏移
    const perpX = dz
    const perpZ = -dx

    for (let i = 0; i < length; i++) {
        if (!isMining) return false
        const base = bot.entity.position.floored()
        const baseY = targetY !== null ? targetY : base.y
        const feetPos  = base.offset(dx, baseY - base.y, dz)
        const headPos  = feetPos.offset(0, 1, 0)
        const feetPos2 = feetPos.offset(perpX, 0, perpZ)
        const headPos2 = feetPos2.offset(0, 1, 0)

        for (const pos of [feetPos, headPos, feetPos2, headPos2]) {
            if (isBuried(pos)) continue
            const b = bot.blockAt(pos)
            if (!b || b.name === 'air' || b.name === 'cave_air') continue
            try {
                await ensureToolFor(bot, b.name)
                await bot.dig(b)
                dug = true
            } catch (_) {}
        }

        _setMovements(bot)
        try {
            await bot.pathfinder.goto(new goals.GoalBlock(feetPos.x, feetPos.y, feetPos.z))
        } catch (_) { break }

        await _sleep(200)
    }
    return dug
}

async function _collectNearby(bot, nearPos, maxDistance) {
    const items = Object.values(bot.entities).filter(e => {
        if (e.name !== 'item') return false
        if (e.position.distanceTo(nearPos) >= maxDistance) return false
        // 跳過被埋起來的物品（位置本身或上方一格有封口標記）
        if (isBuried(e.position.floored())) return false
        if (isBuried(e.position.floored().offset(0, 1, 0))) return false
        return true
    })
    for (const e of items) {
        if (!isMining) return
        try {
            await bot.pathfinder.goto(new goals.GoalNear(e.position.x, e.position.y, e.position.z, 1))
            await _sleep(150)
        } catch (_) {}
    }
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

function isActive() {
    return isMining
}

// 還原 flag 讓 suspended loop 繼續，不啟動新 loop
function resumeMining() {
    isMining = true
}

function getGoal() { return _currentGoal }

module.exports = { startMining, stopMining, isActive, resumeMining, getGoal }
