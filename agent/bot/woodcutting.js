const { goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const activityStack = require('./activity')
const { ensureAxe, ensureToolFor } = require('./crafting')
const bridge = require('./bridge')
const { findSurfaceSpot } = require('./surface')
const { applyMovements, createMovements } = require('./movement_prefs')

let isChopping = false
let _isPaused = false
let _logsCollected = 0
let _loopGen = 0

const SCAFFOLD_BLOCKS = new Set([
    'dirt', 'cobblestone', 'gravel', 'sand', 'stone',
    'oak_planks', 'spruce_planks', 'birch_planks', 'jungle_planks', 'acacia_planks',
])

const VALID_SOIL = new Set(['dirt', 'grass_block', 'podzol', 'mycelium', 'rooted_dirt'])

const LOG_TO_SAPLING = {
    oak_log:      'oak_sapling',
    spruce_log:   'spruce_sapling',
    birch_log:    'birch_sapling',
    jungle_log:   'jungle_sapling',
    acacia_log:   'acacia_sapling',
    dark_oak_log: 'dark_oak_sapling',
    mangrove_log: 'mangrove_propagule',
}

const TREE_SEARCH_RADII = [32, 64]

activityStack.register('chopping', _pause)

function _pause(_bot) {
    isChopping = false
    _isPaused = true
    console.log('[Wood] 暫停砍樹')
}

function _shouldAbort(expectedGen = null) {
    return !isChopping || (expectedGen !== null && _loopGen !== expectedGen)
}

async function _safeEquip(bot, item, slot = 'hand', label = '裝備物品') {
    if (!item) return false
    try {
        if (bot.currentWindow) {
            bot.closeWindow(bot.currentWindow)
            await _sleep(100)
        }
        await bot.equip(item, slot)
        return true
    } catch (e) {
        console.log(`[Wood] ${label}失敗: ${e.message}`)
        return false
    }
}

async function _equipToolForDig(bot, block) {
    if (!block) return
    try {
        await ensureToolFor(bot, block.name)
        return
    } catch (_) {}
    try {
        const tool = bot.pathfinder.bestHarvestTool(block)
        if (tool) await bot.equip(tool, 'hand')
    } catch (_) {}
}

async function startChopping(bot, goal = {}) {
    if (isChopping) {
        if (activityStack.isStale('chopping', 15000)) {
            console.log('[Wood] 偵測到殘留狀態，重置 chopping')
            isChopping = false
            _isPaused = false
            activityStack.forget('chopping')
        } else {
        console.log('[Wood] 已在砍樹中')
        return
        }
    }
    isChopping = true
    _logsCollected = 0
    activityStack.push(bot, 'chopping', goal, (b) => _resumeChopping(b, goal))
    activityStack.markStarted('chopping', 'start')
    console.log('[Wood] 開始砍樹')
    _loop(bot, goal)
}

function _resumeChopping(bot, originalGoal) {
    if (isChopping) return
    const remainingLogs = originalGoal.logs
        ? Math.max(1, originalGoal.logs - _logsCollected)
        : undefined
    isChopping = true
    activityStack.markStarted('chopping', 'resume')
    activityStack.updateTopGoal(remainingLogs
        ? { ...originalGoal, logs: remainingLogs }
        : originalGoal)
    console.log('[Wood] 恢復砍樹')
    _loop(bot, originalGoal)
}

function stopChopping(_bot) {
    if (!isChopping) return
    isChopping = false
    _isPaused = false
    _loopGen++
    activityStack.markStopped('chopping', 'stop')
    console.log('[Wood] 停止砍樹')
}

async function _loop(bot, goal = {}) {
    const _myGen = ++_loopGen
    _isPaused = false
    const skipped = new Set()  // 完全無法到達的樹根位置
    const startTime = Date.now()
    let goalReached = false

    while (isChopping) {
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Wood] 達到時間目標 ${goal.duration}s，停止`)
            isChopping = false
            bridge.sendState(bot, 'activity_done', { activity: 'chopping', reason: 'goal_reached' })
            break
        }
        if (goal.logs && _logsCollected >= goal.logs) {
            console.log(`[Wood] 達到採集目標 ${goal.logs} 根木頭，停止`)
            isChopping = false
            bridge.sendState(bot, 'activity_done', { activity: 'chopping', reason: 'goal_reached' })
            break
        }
        // 如果在地底或水中，先浮出地表再找樹
        if (bot.entity.position.y < 60 || bot.entity.isInWater) {
            console.log(`[Wood] 位置 Y=${Math.floor(bot.entity.position.y)}，先前往地表`)
            const spot = findSurfaceSpot(bot, 24)
            if (spot) {
                try {
                    await Promise.race([
                        bot.pathfinder.goto(new goals.GoalBlock(spot.x, spot.y, spot.z)),
                        new Promise((_, reject) => setTimeout(() => reject(new Error('surface timeout')), 30000)),
                    ])
                    if (_shouldAbort(_myGen)) return
                } catch (_) {}
            }
            if (!isChopping) break
            if (bot.entity.position.y < 60 || bot.entity.isInWater) {
                // Still underground — send stuck so LLM can call surface
                console.log('[Wood] 無法自行回到地表，送出 activity_stuck')
                activityStack.pause(bot)
                bridge.sendState(bot, 'activity_stuck', {
                    activity: 'chopping',
                    reason: 'underground',
                })
                break
            }
        }

        // 每次迴圈檢查斧頭（斧頭壞掉或第一次有材料時自動合成）
        if (!bot.inventory.items().some(i => i.name.endsWith('_axe'))) {
            const ok = await ensureAxe(bot)
            if (!ok) {
                // 沒斧頭也繼續，用徒手砍
                console.log('[Wood] 沒有斧頭，用徒手繼續')
            }
        } else {
            const axe = bot.inventory.items().find(i => i.name.endsWith('_axe'))
            if (axe) await _safeEquip(bot, axe, 'hand', '裝備斧頭')
        }

        let rootPos = null
        for (const radius of TREE_SEARCH_RADII) {
            const candidates = bot.findBlocks({
                matching: b => b.name && b.name.endsWith('_log'),
                maxDistance: radius,
                count: radius > 32 ? 40 : 20,
            })
            rootPos = candidates.find(p => !skipped.has(_posKey(p))) ?? null
            if (rootPos) {
                if (radius > TREE_SEARCH_RADII[0]) {
                    console.log(`[Wood] 近距離無樹，擴大搜尋到 ${radius} 格`)
                }
                break
            }
        }

        if (!rootPos) {
            if (skipped.size > 0) {
                skipped.clear()
                continue
            }
            console.log('[Wood] 附近找不到木頭，送出 activity_stuck')
            // Keep chopping on the activity stack so a recovery action
            // such as surface/back can finish and then resume chopping.
            activityStack.pause(bot)
            bridge.sendState(bot, 'activity_stuck', {
                activity_name: 'chopping',
                reason: 'no_trees',
                suggested_actions: Math.floor(bot.entity.position.y) < 60
                    ? ['surface', 'back', 'home', 'idle']
                    : ['explore', 'home', 'idle'],
                detail: Math.floor(bot.entity.position.y) < 60
                    ? '目前位置偏地下，可能需要先回到地表或前一個位置再砍樹'
                    : '目前附近沒有可砍的樹，可能需要探索其他地表區域',
            })
            break
        }

        // BFS 找整棵樹的所有 log，由下往上排
        const treeBlocks = _findTreeLogs(bot, rootPos)
        const treeLogName = bot.blockAt(treeBlocks[0])?.name ?? null
        console.log(`[Wood] 找到樹（${treeLogName}），共 ${treeBlocks.length} 個木頭`)

        const groundY = Math.floor(bot.entity.position.y)
        let reachedAny = false

        for (const pos of treeBlocks) {
            if (!isChopping) return

            const block = bot.blockAt(pos)
            if (!block || !block.name.endsWith('_log')) continue
            activityStack.touch('chopping', 'tree_target')

            const movements = createMovements(bot, { canDig: true })
            // Only allow digging leaves — not stone/ore — while navigating to tree
            movements.blocksToAvoid = new Set()
            movements.canDigBlock = (block) => {
                if (block?.name?.includes('leaves')) return true
                return false
            }
            bot.pathfinder.setMovements(movements)

            // 先水平走近（遇到樹葉可以挖開，但不挖礦石）
            try {
                await bot.pathfinder.goto(new goals.GoalNear(pos.x, pos.y, pos.z, 3))
                if (_shouldAbort(_myGen)) return
            } catch (e) { /* 走不到也繼續，試試疊方塊 */ }

            // 如果目標比現在高超過 2 格，手動疊方塊上去
            const heightDiff = pos.y - Math.floor(bot.entity.position.y)
            if (heightDiff > 2) {
                await _pillarUp(bot, pos.y, pos)
                if (_shouldAbort(_myGen)) return
            }

            reachedAny = true

            if (!isChopping) return

            const fresh = bot.blockAt(pos)
            if (!fresh || !fresh.name.endsWith('_log')) continue

            await ensureToolFor(bot, fresh.name)

            try {
                await bot.dig(fresh)
                if (_shouldAbort(_myGen)) return
                _logsCollected++
                activityStack.touch('chopping', 'collected_log')
                console.log(`[Wood] 砍下 ${fresh.name} at y=${pos.y}（共 ${_logsCollected} 根）`)
                activityStack.updateProgress({ logs: _logsCollected })
                await _sleep(500)
                if (_shouldAbort(_myGen)) return
                await _collectNearby(bot, pos, 4)
                if (_shouldAbort(_myGen)) return
                if (goal.logs && _logsCollected >= goal.logs) {
                    console.log(`[Wood] 達到採集目標 ${goal.logs} 根木頭，停止`)
                    goalReached = true
                    isChopping = false
                    bridge.sendState(bot, 'activity_done', { activity: 'chopping', reason: 'goal_reached' })
                    break
                }
            } catch (e) {
                console.log('[Wood] 砍樹失敗:', e.message)
                await _sleep(400)
            }
        }

        // 整棵樹一個都到不了，才把 rootPos 標記為跳過
        if (!reachedAny) {
            skipped.add(_posKey(rootPos))
            continue
        }

        // 整棵樹砍完後才回收疊腳方塊、安全下來
        if (Math.floor(bot.entity.position.y) > groundY) {
            await _reclaimScaffold(bot, groundY)
            if (_shouldAbort(_myGen)) return
        }
        if (Math.floor(bot.entity.position.y) > groundY) {
            applyMovements(bot, { canDig: false })
            const botPos = bot.entity.position
            try {
                await bot.pathfinder.goto(
                    new goals.GoalNear(Math.floor(botPos.x), groundY, Math.floor(botPos.z), 2)
                )
                if (_shouldAbort(_myGen)) return
            } catch (e) {
                console.log('[Wood] 無法安全下來，繼續')
            }
        }

        // 砍完後：撿附近掉落物、種樹苗
        await _collectNearby(bot, rootPos, 8)
        if (_shouldAbort(_myGen)) return
        await _plantSapling(bot, rootPos, treeLogName)
        if (_shouldAbort(_myGen)) return
        if (goalReached) break
    }

    if (!_isPaused && _loopGen === _myGen) activityStack.pop(bot)
    _isPaused = false
}

// BFS 找所有相連的 log block（同一棵樹），由下往上排序
function _findTreeLogs(bot, rootPos) {
    const visited = new Set()
    const queue = [rootPos]
    const result = []

    while (queue.length > 0) {
        const pos = queue.shift()
        const key = _posKey(pos)
        if (visited.has(key)) continue
        visited.add(key)

        const block = bot.blockAt(pos)
        if (!block || !block.name.endsWith('_log')) continue

        result.push(pos)

        const offsets = [[0,1,0],[0,-1,0],[1,0,0],[-1,0,0],[0,0,1],[0,0,-1]]
        for (const [dx, dy, dz] of offsets) {
            const next = pos.offset(dx, dy, dz)
            if (!visited.has(_posKey(next))) queue.push(next)
        }
    }

    return result.sort((a, b) => a.y - b.y)
}

// 疊方塊往上：看下 → 跳 → 馬上放方塊，連續動作不需要確認時機
async function _pillarUp(bot, targetY, targetPos) {
    while (Math.floor(bot.entity.position.y) < targetY - 1) {
        if (!isChopping) return
        const scaffold = bot.inventory.items().find(i => SCAFFOLD_BLOCKS.has(i.name))
        if (!scaffold) { console.log('[Wood] 沒有疊腳材料'); break }

        if (!await _safeEquip(bot, scaffold, 'hand', '裝備疊腳材料')) break
        await bot.look(bot.entity.yaw, Math.PI / 2, true)  // 看正下方

        const below = bot.blockAt(bot.entity.position.floored().offset(0, -1, 0))
        if (!below || below.name === 'air') break

        // 疊腳目標位置（bot 腳底的那格）必須是空氣才能放
        const placeTarget = bot.blockAt(below.position.offset(0, 1, 0))
        if (placeTarget && placeTarget.name !== 'air') {
            if (placeTarget.name.includes('leaves')) {
                try { await _equipToolForDig(bot, placeTarget); await bot.dig(placeTarget) } catch (_) {}  // 樹葉先挖掉
            } else {
                break  // 木頭或其他實心方塊，已夠近，停止疊腳
            }
        }

        // 確認頭上有空間可以跳（bot 高 2 格，需要 +2 格是空的）
        const headBlock = bot.blockAt(bot.entity.position.floored().offset(0, 2, 0))
        if (headBlock && headBlock.name !== 'air') {
            if (headBlock.name.includes('leaves')) {
                try { await _equipToolForDig(bot, headBlock); await bot.dig(headBlock) } catch (_) {}
            } else {
                break  // 頭上有實心方塊，跳不起來
            }
        }

        const beforeY = bot.entity.position.y

        bot.setControlState('jump', true)
        try { await bot.placeBlock(below, new Vec3(0, 1, 0)) } catch (_) {}
        bot.setControlState('jump', false)

        await _sleep(400)

        // 放方塊失敗（位置沒變），不要繼續跳
        if (bot.entity.position.y <= beforeY + 0.1) break
        // 目標已在攻擊範圍內，不需再疊（需同時確認高度夠近才停止）
        if (targetPos) {
            const _dy = targetPos.y - bot.entity.position.y
            if (_dy <= 1.5 && bot.entity.position.distanceTo(targetPos) <= 4.5) break
        }
    }
}

// 挖回所有疊腳方塊（SCAFFOLD_BLOCKS 都回收）
async function _reclaimScaffold(bot, groundY) {
    while (true) {
        if (Math.floor(bot.entity.position.y) <= groundY) break
        const below = bot.blockAt(bot.entity.position.floored().offset(0, -1, 0))
        if (!below || !SCAFFOLD_BLOCKS.has(below.name)) break
        try {
            await ensureToolFor(bot, below.name)
            await bot.dig(below)
            console.log(`[Wood] 回收 ${below.name}`)
            await _sleep(300)
        } catch (e) {
            break
        }
    }
    // 挖完疊腳方塊後換回斧頭
    const axe = bot.inventory.items().find(i => i.name.endsWith('_axe'))
    if (axe) await _safeEquip(bot, axe, 'hand', '換回斧頭')
}

// 撿起附近掉落的物品
async function _collectNearby(bot, nearPos, maxDistance) {
    const items = Object.values(bot.entities)
        .filter(e => e.name === 'item' && e.position.distanceTo(nearPos) < maxDistance)
        .sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))

    for (const e of items) {
        if (!isChopping) return
        await _collectDrop(bot, e)
    }
}

async function _collectDrop(bot, drop) {
    const initialPos = drop.position.clone()
    for (let attempt = 0; attempt < 3 && isChopping; attempt++) {
        try {
            await bot.pathfinder.goto(
                new goals.GoalNear(drop.position.x, drop.position.y, drop.position.z, 1)
            )
            await _sleep(250)
        } catch (e) {
            if (await _clearLeavesToward(bot, drop.position)) {
                continue
            }
            console.log(`[Wood] 無法撿取掉落物: ${e.message}`)
            break
        }

        const stillThere = Object.values(bot.entities).find(
            ent => ent.id === drop.id || (ent.name === 'item' && ent.position.distanceTo(initialPos) < 1.2)
        )
        if (!stillThere) return

        if (!await _clearLeavesToward(bot, stillThere.position)) {
            break
        }
    }
}

async function _clearLeavesToward(bot, targetPos) {
    const candidates = []
    const base = bot.entity.position.floored()
    for (let dx = -1; dx <= 1; dx++) {
        for (let dy = 0; dy <= 1; dy++) {
            for (let dz = -1; dz <= 1; dz++) {
                const pos = base.offset(dx, dy, dz)
                const block = bot.blockAt(pos)
                if (!block || !block.name?.includes('leaves')) continue
                const score = block.position.distanceTo(targetPos) + block.position.distanceTo(bot.entity.position)
                candidates.push({ block, score })
            }
        }
    }

    if (candidates.length === 0) return false
    candidates.sort((a, b) => a.score - b.score)

    for (const { block } of candidates.slice(0, 2)) {
        try {
            await _equipToolForDig(bot, block)
            await bot.dig(block)
            console.log(`[Wood] 挖開 ${block.name} 以撿取掉落物`)
            await _sleep(200)
            return true
        } catch (_) {}
    }
    return false
}

// 在砍完的樹根位置種樹苗
async function _plantSapling(bot, rootPos, logName) {
    if (!logName) return
    const saplingName = LOG_TO_SAPLING[logName]
    if (!saplingName) return
    const sapling = bot.inventory.items().find(i => i.name === saplingName)
    if (!sapling) return

    // 樹根最底層往下一格應該是泥土/草地
    const groundBlock = bot.blockAt(rootPos.offset(0, -1, 0))
    if (!groundBlock || !VALID_SOIL.has(groundBlock.name)) return

    // 種植位置（rootPos）需要是空氣
    const plantSpot = bot.blockAt(rootPos)
    if (!plantSpot || plantSpot.name !== 'air') return

    try {
        await bot.pathfinder.goto(
            new goals.GoalNear(rootPos.x, groundBlock.position.y, rootPos.z, 2)
        )
        if (!await _safeEquip(bot, sapling, 'hand', '裝備樹苗')) return
        await bot.placeBlock(groundBlock, new Vec3(0, 1, 0))
        console.log(`[Wood] 種下 ${saplingName}`)
    } catch (e) {
        console.log(`[Wood] 種樹苗失敗: ${e.message}`)
    }
}

function _posKey(pos) {
    return `${Math.floor(pos.x)},${Math.floor(pos.y)},${Math.floor(pos.z)}`
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

function isActive() {
    return isChopping
}

module.exports = { startChopping, stopChopping, isActive }
