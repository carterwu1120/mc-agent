const { goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const activityStack = require('./activity')
const bridge = require('./bridge')

let isSmelting = false
let _isPaused = false
let _smeltedCount = 0
let _inputPending = false
let _placedFurnacePos = null
let _loopGen = 0
let _lastOutcome = null
let _gotoFurnaceFailures = 0
let _coalBlockUncrackTried = false

const SMELTABLE = {
    raw_iron: 'iron_ingot',         iron_ore: 'iron_ingot',         deepslate_iron_ore: 'iron_ingot',
    raw_gold: 'gold_ingot',         gold_ore: 'gold_ingot',         deepslate_gold_ore: 'gold_ingot',
    raw_copper: 'copper_ingot',     copper_ore: 'copper_ingot',     deepslate_copper_ore: 'copper_ingot',
    beef: 'cooked_beef',            porkchop: 'cooked_porkchop',    chicken: 'cooked_chicken',
    mutton: 'cooked_mutton',        rabbit: 'cooked_rabbit',        cod: 'cooked_cod',
    salmon: 'cooked_salmon',        potato: 'baked_potato',
    sand: 'glass',                  cobblestone: 'stone',
}

// Map cooked/output names back to raw input names (for LLM commands like "smelt steak")
const SMELT_ALIAS = Object.fromEntries(
    Object.entries(SMELTABLE).map(([raw, cooked]) => [cooked, raw])
)
// Also map common English food aliases
Object.assign(SMELT_ALIAS, {
    steak: 'beef',
    iron: 'raw_iron',
    gold: 'raw_gold',
    copper: 'raw_copper',
})

const COAL_BLOCK_UNCRAFT_THRESHOLD = 16  // ops; if neededUnits < this, try to uncraft coal_block → coal first

const FUEL_PRIORITY = [
    'coal', 'charcoal',
    'oak_log', 'spruce_log', 'birch_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'mangrove_log',
    'oak_planks', 'spruce_planks', 'birch_planks', 'jungle_planks', 'acacia_planks', 'dark_oak_planks',
    'coal_block',
]

const FUEL_UNITS = {
    coal: 8,
    charcoal: 8,
    coal_block: 80,
    oak_log: 1.5,
    spruce_log: 1.5,
    birch_log: 1.5,
    jungle_log: 1.5,
    acacia_log: 1.5,
    dark_oak_log: 1.5,
    mangrove_log: 1.5,
    oak_planks: 1.5,
    spruce_planks: 1.5,
    birch_planks: 1.5,
    jungle_planks: 1.5,
    acacia_planks: 1.5,
    dark_oak_planks: 1.5,
}

const REPLACEABLE_BLOCKS = new Set([
    'air', 'cave_air', 'short_grass', 'grass', 'tall_grass', 'fern', 'large_fern',
    'dead_bush', 'snow', 'vine', 'torch', 'wall_torch',
])

activityStack.register('smelting', _pause)

function _pause(_bot) {
    isSmelting = false
    _isPaused = true
    console.log('[Smelt] 暫停燒製')
}

function _shouldAbort(expectedGen = null) {
    return !isSmelting || (expectedGen !== null && _loopGen !== expectedGen)
}

function _shouldAbortFinalize(expectedGen = null) {
    return _isPaused || (expectedGen !== null && _loopGen !== expectedGen)
}

function _setOutcome(status, extra = {}) {
    _lastOutcome = { status, at: Date.now(), ...extra }
}

function _getPreferredFuelItem(bot, neededUnits = Infinity) {
    const inventory = bot.inventory.items()
    for (const fuelName of FUEL_PRIORITY) {
        if (fuelName === 'coal_block' && neededUnits < COAL_BLOCK_UNCRAFT_THRESHOLD && !_coalBlockUncrackTried) continue
        const item = inventory.find(i => i.name === fuelName)
        if (item) return item
    }
    return null
}

async function _tryUncrackCoalBlock(bot) {
    if (!bot.inventory.items().some(i => i.name === 'coal_block')) return false
    const { ensureCraftingTable } = require('./crafting')
    const table = await ensureCraftingTable(bot)
    if (!table) return false
    const coalItem = bot.registry.itemsByName['coal']
    if (!coalItem) return false
    const recipe = bot.recipesFor(coalItem.id, null, 1, table)[0]
    if (!recipe) {
        console.log('[Smelt] 找不到 coal_block → coal 配方')
        return false
    }
    try {
        await bot.craft(recipe, 1, table)
        console.log('[Smelt] 解開 coal_block → 9 coal')
        return true
    } catch (e) {
        console.log('[Smelt] 解開 coal_block 失敗:', e.message)
        return false
    }
}

function _estimateFuelCount(goal, smeltedCount, inputPending) {
    if (!goal?.count) return 8
    const remaining = Math.max(1, goal.count - smeltedCount)
    return inputPending ? Math.max(1, remaining) : remaining
}

function _getFuelInsertCount(fuelItem, goal, smeltedCount, inputPending) {
    const perItemUnits = FUEL_UNITS[fuelItem.name] ?? 1
    const neededUnits = _estimateFuelCount(goal, smeltedCount, inputPending)
    return Math.max(1, Math.min(fuelItem.count, Math.ceil(neededUnits / perItemUnits), 16))
}

function consumeLastOutcome(maxAgeMs = 10000) {
    if (!_lastOutcome) return null
    const outcome = _lastOutcome
    if ((Date.now() - outcome.at) > maxAgeMs) {
        _lastOutcome = null
        return null
    }
    _lastOutcome = null
    return outcome
}

async function startSmelting(bot, goal = {}) {
    if (isSmelting) {
        if (activityStack.isStale('smelting', 20000)) {
            console.log('[Smelt] 偵測到殘留狀態，重置 smelting')
            isSmelting = false
            _isPaused = false
            _inputPending = false
            _placedFurnacePos = null
            activityStack.forget('smelting')
        } else {
        console.log('[Smelt] 已在燒製中')
        return
        }
    }
    isSmelting = true
    _smeltedCount = 0
    _inputPending = false
    _lastOutcome = null
    _gotoFurnaceFailures = 0
    _coalBlockUncrackTried = false
    activityStack.push(bot, 'smelting', goal, (b) => _resumeSmelting(b, goal))
    activityStack.markStarted('smelting', 'start')
    console.log(`[Smelt] 開始燒製 goal=${JSON.stringify(goal)}`)
    _loop(bot, goal)
}

function _resumeSmelting(bot, originalGoal) {
    if (isSmelting) return
    const remainingCount = originalGoal.count
        ? Math.max(1, originalGoal.count - _smeltedCount)
        : undefined
    isSmelting = true
    activityStack.markStarted('smelting', 'resume')
    activityStack.updateTopGoal(remainingCount
        ? { ...originalGoal, count: remainingCount }
        : originalGoal)
    console.log('[Smelt] 恢復燒製')
    _loop(bot, originalGoal)
}

function stopSmelting(_bot) {
    if (!isSmelting) return
    isSmelting = false
    _isPaused = false
    _loopGen++
    _setOutcome('stopped', { reason: 'stop' })
    activityStack.markStopped('smelting', 'stop')
    console.log('[Smelt] 停止燒製')
}

function isActive() {
    return isSmelting
}

async function _loop(bot, goal = {}) {
    const _myGen = ++_loopGen
    _isPaused = false
    const startTime = Date.now()

    while (isSmelting) {
        // 停止條件
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Smelt] 達到時間目標 ${goal.duration}s，停止`)
            isSmelting = false
            _setOutcome('done', { reason: 'goal_reached', goal })
            bridge.sendState(bot, 'activity_done', { activity: 'smelting', reason: 'goal_reached' })
            break
        }
        if (goal.target && goal.count && _smeltedCount >= goal.count) {
            console.log(`[Smelt] 達到目標 ${goal.target} x${goal.count}，停止`)
            isSmelting = false
            _setOutcome('done', { reason: 'goal_reached', goal })
            bridge.sendState(bot, 'activity_done', { activity: 'smelting', reason: 'goal_reached' })
            break
        }

        // 找或放置熔爐
        const furnaceBlock = await _findOrPlaceFurnace(bot)
        if (!furnaceBlock) {
            console.log('[Smelt] 找不到熔爐，停止')
            isSmelting = false
            if (!_lastOutcome) {
                _setOutcome('stuck', { reason: 'missing_dependency', goal })
                // Notify Python so it can plan a recovery (e.g. chop wood first).
                // Without this, mining immediately resumes and re-triggers smelting → tight loop.
                bridge.sendState(bot, 'activity_stuck', {
                    activity: 'smelting',
                    reason: 'missing_dependency',
                    detail: '找不到或無法放置熔爐（可能缺木材合成工作檯）',
                    smelt_item: goal.target,
                    smelt_count: goal.count,
                })
            }
            break
        }

        // 走過去
        try {
            const p = furnaceBlock.position
            activityStack.touch('smelting', 'goto_furnace')
            await bot.pathfinder.goto(new goals.GoalNear(p.x, p.y, p.z, 2))
            _gotoFurnaceFailures = 0
            if (_shouldAbort(_myGen)) return
        } catch (e) {
            console.log('[Smelt] 無法走到熔爐:', e.message)
            _gotoFurnaceFailures += 1
            if (_gotoFurnaceFailures >= 3) {
                try {
                    const { ensurePickaxe } = require('./crafting')
                    await ensurePickaxe(bot)
                } catch (_) {}
                const nearPlaced = await _placeFurnace(bot, bot.registry.blocksByName['furnace']?.id, { preferNearby: true })
                if (nearPlaced) {
                    console.log('[Smelt] 連續無法走到既有熔爐，改為就地放置新熔爐')
                    _gotoFurnaceFailures = 0
                }
            }
            await _sleep(2000)
            if (_shouldAbort(_myGen)) return
            continue
        }

        if (!isSmelting) break

        // 開啟熔爐
        let furnace
        try {
            furnace = await bot.openFurnace(furnaceBlock)
            await _sleep(200)  // 等 window sync
            if (_shouldAbort(_myGen)) {
                try { furnace.close() } catch (_) {}
                return
            }
        } catch (e) {
            console.log('[Smelt] 開啟熔爐失敗:', e.message)
            await _sleep(2000)
            if (_shouldAbort(_myGen)) return
            continue
        }

        try {
            // 若熔爐有遺留的成品先取出（slots[2] = output slot）
            const prevOut = furnace.slots[2]
            if (prevOut) {
                try {
                    await furnace.takeOutput()
                    if (_shouldAbort(_myGen)) {
                        try { furnace.close() } catch (_) {}
                        return
                    }
                    _smeltedCount += prevOut.count
                    activityStack.touch('smelting', 'take_output')
                    _inputPending = !!furnace.slots[0]
                    activityStack.updateProgress({ smelted: _smeltedCount })
                    console.log(`[Smelt] 取出遺留產物 ${prevOut.name} x${prevOut.count}（共 ${_smeltedCount}）`)
                } catch (e) {
                    const invSlots = bot.inventory.items().length
                    if (invSlots >= 36) {
                        console.log('[Smelt] 背包已滿，執行整理...')
                        furnace.close()
                        isSmelting = false
                        _isPaused = false
                        activityStack.pop(bot)
                        const { handleFull } = require('./inventory')
                        await handleFull(bot)
                        return
                    }
                    console.log(`[Smelt] takeOutput 失敗（${invSlots}/36）: ${e.message}`)
                }
            }

            // 找背包裡可燒的材料
            const rawTarget = goal.target ? (SMELT_ALIAS[goal.target] || goal.target) : null
            const smeltableItems = bot.inventory.items().filter(i => {
                if (!SMELTABLE[i.name]) return false
                if (rawTarget) return i.name === rawTarget || i.name.includes(rawTarget)
                return true
            })

            // 沒材料且沒有在燒 → 停止
            if (smeltableItems.length === 0 && !_inputPending) {
                console.log('[Smelt] 背包沒有可燒的材料，停止')
                furnace.close()
                isSmelting = false
                _setOutcome('stuck', { reason: 'no_input', goal })
                bridge.sendState(bot, 'activity_stuck', { activity: 'smelting', reason: 'no_input' })
                break
            }

            // 加燃料（fuel 是進度值 0-1；為 0 且 slots[1] 無煤才補）
            if (!furnace.fuel && !furnace.slots[1]) {
                const neededUnits = _estimateFuelCount(goal, _smeltedCount, _inputPending)
                let fuelItem = _getPreferredFuelItem(bot, neededUnits)

                // 需求量小、無較精細燃料、但有 coal_block → 先嘗試解開成 coal
                if (!fuelItem && neededUnits < COAL_BLOCK_UNCRAFT_THRESHOLD
                        && !_coalBlockUncrackTried
                        && bot.inventory.items().some(i => i.name === 'coal_block')) {
                    furnace.close()
                    if (_shouldAbort(_myGen)) return
                    const uncrafted = await _tryUncrackCoalBlock(bot)
                    _coalBlockUncrackTried = true  // 無論成敗，下次直接用 coal_block，避免迴圈
                    if (_shouldAbort(_myGen)) return
                    continue  // 重新走熔爐流程：成功 → 用 coal；失敗 → _getPreferredFuelItem 允許 coal_block
                }

                if (fuelItem) {
                    const fuelCount = _getFuelInsertCount(fuelItem, goal, _smeltedCount, _inputPending)
                    try {
                        await furnace.putFuel(fuelItem.type, null, fuelCount)
                        if (_shouldAbort(_myGen)) {
                            try { furnace.close() } catch (_) {}
                            return
                        }
                        activityStack.touch('smelting', 'put_fuel')
                        console.log(`[Smelt] 放入燃料 ${fuelItem.name} x${fuelCount}`)
                    } catch (e) {
                        if (!e.message?.includes('destination full')) throw e
                        console.log('[Smelt] fuel slot 已有燃料，跳過')
                    }
                } else {
                    console.log('[Smelt] 背包沒有燃料，通知 Python 決策')
                    isSmelting = false
                    _setOutcome('stuck', { reason: 'no_fuel', goal })
                    try { furnace.close() } catch (_) {}
                    bridge.sendState(bot, 'activity_stuck', { activity: 'smelting', reason: 'no_fuel' })
                    break
                }
            }

            // 放入材料（未放過才放）
            if (!_inputPending && smeltableItems.length > 0) {
                const inputItem = smeltableItems[0]
                const totalAvail = smeltableItems
                    .filter(i => i.name === inputItem.name)
                    .reduce((s, i) => s + i.count, 0)
                let count = totalAvail
                if (goal.count) count = Math.min(count, goal.count - _smeltedCount)
                count = Math.min(count, 64)
                if (count > 0) {
                    try {
                        await furnace.putInput(inputItem.type, null, count)
                        if (_shouldAbort(_myGen)) {
                            try { furnace.close() } catch (_) {}
                            return
                        }
                        _inputPending = true
                        activityStack.touch('smelting', 'put_input')
                        console.log(`[Smelt] 放入 ${inputItem.name} x${count}（背包共 ${totalAvail}${goal.count ? `，目標剩 ${goal.count - _smeltedCount}` : ''}）`)
                    } catch (e) {
                        if (!e.message?.includes('destination full')) throw e
                        _inputPending = true
                        console.log('[Smelt] input slot 已有材料，等待燒製完成...')
                    }
                }
            }

            furnace.close()

            // 每 10 秒重開熔爐，輪詢 slots[2] 有無產物，直到爐膛清空或達到目標
            // timeout 以「距上次取出產物的時間」計算，避免大批材料被誤判為超時
            let lastProgressAt = Date.now()
            let pollTries = 0
            while (_inputPending && isSmelting) {
                for (let i = 0; i < 10 && isSmelting; i++) await _sleep(1000)
                if (!isSmelting) break
                if (_shouldAbort(_myGen)) return
                if (Date.now() - lastProgressAt > 60000) {
                    console.log('[Smelt] 60s 無進度，放棄本批')
                    _inputPending = false
                    break
                }

                try {
                    furnace = await bot.openFurnace(furnaceBlock)
                    await _sleep(300)
                    if (_shouldAbort(_myGen)) {
                        try { furnace.close() } catch (_) {}
                        return
                    }
                } catch (e) {
                    console.log('[Smelt] 重開熔爐失敗:', e.message)
                    break
                }

                const outputItem = furnace.slots[2]
                if (outputItem) {
                    try {
                        await furnace.takeOutput()
                        if (_shouldAbort(_myGen)) {
                            try { furnace.close() } catch (_) {}
                            return
                        }
                        _smeltedCount += outputItem.count
                        _inputPending = !!furnace.slots[0]
                        lastProgressAt = Date.now()
                        activityStack.updateProgress({ smelted: _smeltedCount })
                        console.log(`[Smelt] 取出 ${outputItem.name} x${outputItem.count}（共 ${_smeltedCount}，爐膛剩餘: ${furnace.slots[0]?.count ?? 0}）`)
                    } catch (e) {
                        const invSlots = bot.inventory.items().length
                        if (invSlots >= 36) {
                            furnace.close()
                            isSmelting = false
                            _isPaused = false
                            activityStack.pop(bot)
                            const { handleFull } = require('./inventory')
                            await handleFull(bot)
                            return
                        }
                        console.log(`[Smelt] takeOutput 失敗（${invSlots}/36）: ${e.message}`)
                    }
                } else {
                    pollTries++
                    console.log(`[Smelt] 燒製中... (${pollTries * 10}s)`)
                    const hasInput = !!furnace.slots[0]
                    const hasFuelSlot = !!furnace.slots[1]
                    const isBurning = !!furnace.fuel

                    // 熔爐仍有材料且尚可繼續燃燒時，回報 heartbeat，避免 watchdog
                    // 將正常等待下一個成品的過程誤判成 stuck。
                    if (hasInput && (isBurning || hasFuelSlot)) {
                        activityStack.touch('smelting', isBurning ? 'burning' : 'waiting_fuel_consumption')
                    }

                    // 若爐子既沒有輸入槽材料也沒有燃料，代表已燒完或燃料耗盡
                    if (!hasInput && !hasFuelSlot && !isBurning) {
                        console.log('[Smelt] 爐膛已空（無輸入、無燃料），結束本批')
                        _inputPending = false
                        furnace.close()
                        break
                    }
                }

                if (goal.target && goal.count && _smeltedCount >= goal.count) {
                    _inputPending = false
                    break
                }

                furnace.close()
            }

            // 本批完成，回收燃料
            if (!_inputPending) {
                try {
                    furnace = await bot.openFurnace(furnaceBlock)
                    await _sleep(300)
                    if (_shouldAbort(_myGen)) {
                        try { furnace.close() } catch (_) {}
                        return
                    }
                    if (furnace.slots[1]) {
                        await furnace.takeFuel()
                        if (_shouldAbort(_myGen)) {
                            try { furnace.close() } catch (_) {}
                            return
                        }
                        console.log('[Smelt] 回收燃料')
                    }
                    furnace.close()
                } catch (_) {}
            } else {
                try { furnace.close() } catch (_) {}
            }
        } catch (e) {
            console.log('[Smelt] 燒製操作失敗:', e.message)
            try { furnace.close() } catch (_) {}
            if (e.message?.includes('destination full')) {
                const slots = bot.inventory.items().length
                if (slots >= 36) {
                    isSmelting = false
                    _isPaused = false
                    activityStack.pop(bot)
                    const { handleFull } = require('./inventory')
                    await handleFull(bot)
                    return
                }
                await _sleep(500)
                if (_shouldAbort(_myGen)) return
                continue
            }
        }
    }

    if (!_isPaused && _loopGen === _myGen) {
        const hadPlaced = !!_placedFurnacePos
        await _reclaimFurnace(bot)
        if (_shouldAbortFinalize(_myGen)) return
        if (hadPlaced && !bot.inventory.items().some(i => i.name === 'furnace')) {
            console.log('[Smelt] 背包未收到熔爐，嘗試補撿...')
            for (let attempt = 0; attempt < 8; attempt++) {
                await _sleep(500)
                if (_shouldAbortFinalize(_myGen)) return
                if (bot.inventory.items().some(i => i.name === 'furnace')) break
                const dropped = Object.values(bot.entities).find(
                    e => e.name === 'item' && e.position.distanceTo(bot.entity.position) < 5
                )
                if (dropped) {
                    try {
                        await bot.pathfinder.goto(new goals.GoalNear(
                            dropped.position.x, dropped.position.y, dropped.position.z, 0
                        ))
                        if (_shouldAbortFinalize(_myGen)) return
                    } catch (_) {}
                }
            }
            if (bot.inventory.items().some(i => i.name === 'furnace')) {
                console.log('[Smelt] 熔爐已收回')
            } else {
                console.log('[Smelt] 無法收回熔爐，繼續')
            }
        }
        activityStack.pop(bot)
    }
    _isPaused = false
}

async function _findOrPlaceFurnace(bot) {
    const furnaceId    = bot.registry.blocksByName['furnace']?.id
    const litFurnaceId = bot.registry.blocksByName['lit_furnace']?.id
    if (!furnaceId) return null

    // 找現有熔爐
    const existing = bot.findBlock({
        matching: b => b.type === furnaceId || (litFurnaceId && b.type === litFurnaceId),
        maxDistance: 32,
    })
    if (existing) return existing

    // 背包沒有熔爐 → 嘗試合成
    if (!bot.inventory.items().some(i => i.name === 'furnace')) {
        const furnaceStone = _countItem(bot, 'cobblestone') + _countItem(bot, 'cobbled_deepslate')
        if (furnaceStone < 8) {
            const needed = 8 - furnaceStone
            console.log(`[Smelt] 沒有熔爐且爐材不足，交由 activity_stuck 決定下一步（還差 ${needed}）`)
            _reportStuck(bot, {
                reason: 'missing_dependency',
                missing: ['furnace_stone'],
                needed_for: 'furnace',
                missing_count: needed,
                suggested_actions: ['mine', 'home', 'withdraw'],
                detail: `缺少 ${needed} 個可做熔爐的石材（cobblestone / cobbled_deepslate），無法合成熔爐`,
            })
            return null
        }
        const crafted = await _craftFurnace(bot)
        if (!crafted) return null
    }

    return await _placeFurnace(bot, furnaceId)
}

function _countItem(bot, name) {
    return bot.inventory.items()
        .filter(i => i.name === name)
        .reduce((s, i) => s + i.count, 0)
}

function _reportStuck(bot, stuck) {
    _setOutcome('stuck', { reason: stuck.reason, goal: activityStack.getStack().slice(-1)[0]?.goal || null, stuck })
    bridge.sendState(bot, 'activity_stuck', {
        activity: 'smelting',
        ...stuck,
    })
}

async function _craftFurnace(bot) {
    const { ensureCraftingTable } = require('./crafting')
    const table = await ensureCraftingTable(bot)
    if (!table) return false

    const item = bot.registry.itemsByName['furnace']
    if (!item) return false
    const recipe = bot.recipesFor(item.id, null, 1, table)[0]
    if (!recipe) {
        console.log('[Smelt] 找不到熔爐合成配方')
        return false
    }
    try {
        await bot.craft(recipe, 1, table)
        console.log('[Smelt] 合成熔爐')
        return true
    } catch (e) {
        console.log('[Smelt] 合成熔爐失敗:', e.message)
        return false
    }
}

async function _placeFurnace(bot, furnaceId) {
    const item = bot.inventory.items().find(i => i.name === 'furnace')
    if (!item) return null
    await bot.equip(item, 'hand')

    const pos = bot.entity.position.floored()
    const dirs = [[1,0],[0,1],[-1,0],[0,-1]]

    // Place 2 blocks ahead in each cardinal direction (same logic as crafting table)
    const candidates = []
    for (const [dx, dz] of dirs) {
        const spacePos = pos.offset(dx * 2, 0, dz * 2)
        const ground = bot.blockAt(spacePos.offset(0, -1, 0))
        if (!ground || ground.boundingBox !== 'block') continue
        const space = bot.blockAt(spacePos)
        const isOpen = space && REPLACEABLE_BLOCKS.has(space.name)
        candidates.push({ ground, spacePos, needDig: !isOpen, space })
    }

    candidates.sort((a, b) => a.needDig - b.needDig)

    for (const { ground, spacePos, needDig, space } of candidates) {
        if (needDig) {
            if (!space || space.boundingBox !== 'block') continue
            try {
                const { ensureToolFor } = require('./crafting')
                await ensureToolFor(bot, space.name)
            } catch (_) {}
            try { await bot.dig(space) } catch (_) { continue }
            const fresh = bot.blockAt(spacePos)
            if (!fresh || !REPLACEABLE_BLOCKS.has(fresh.name)) continue
        }

        try {
            await bot.equip(item, 'hand')
            await bot.lookAt(ground.position.offset(0.5, 1, 0.5))
            try {
                await bot.placeBlock(ground, new Vec3(0, 1, 0))
            } catch (_) {
                // blockUpdate 事件超時 — 仍需確認方塊是否實際放置成功
            }
            await _sleep(400)
            const placed = bot.findBlock({ matching: furnaceId, maxDistance: 4 })
            if (placed) {
                console.log('[Smelt] 放置熔爐')
                _placedFurnacePos = placed.position.clone()
                return placed
            }
            console.log('[Smelt] 放置熔爐失敗:', spacePos)
        } catch (e) {
            console.log('[Smelt] 放置熔爐失敗:', e.message)
        }
    }

    console.log('[Smelt] 找不到放置位置')
    return null
}

async function _reclaimFurnace(bot) {
    if (!_placedFurnacePos) return
    const pos = _placedFurnacePos
    _placedFurnacePos = null
    const furnaceId    = bot.registry.blocksByName['furnace']?.id
    const litFurnaceId = bot.registry.blocksByName['lit_furnace']?.id
    const block = bot.blockAt(pos)
    if (!block || (block.type !== furnaceId && block.type !== litFurnaceId)) return
    try {
        const { ensurePickaxe } = require('./crafting')
        await ensurePickaxe(bot)
        await bot.dig(block)
        await _sleep(400)
        // 撿起掉落的熔爐
        const dropped = Object.values(bot.entities).find(
            e => e.name === 'item' && e.position.distanceTo(pos) < 3
        )
        if (dropped) {
            try {
                await bot.pathfinder.goto(new goals.GoalNear(dropped.position.x, dropped.position.y, dropped.position.z, 0))
            } catch (_) {}
        }
        console.log('[Smelt] 回收熔爐')
    } catch (e) {
        console.log('[Smelt] 回收熔爐失敗:', e.message)
    }
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

module.exports = { startSmelting, stopSmelting, isActive, consumeLastOutcome }
