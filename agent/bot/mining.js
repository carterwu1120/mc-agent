const execution = require('./execution')
const { goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const activityStack = require('./activity')
const { ensureToolFor, ensurePickaxeTier, ensurePickaxe } = require('./crafting')
const bridge = require('./bridge')
const { sendActivityDone } = bridge
const { isBuried } = require('./buried')
const eating = require('./eating')
const water = require('./water')
const hazards = require('./hazards')
const { applyMovements } = require('./movement_prefs')
const navigation = require('./navigation')
const { AsyncLocalStorage } = require('node:async_hooks')
const loopContext = new AsyncLocalStorage()
const miningPhase = require('./mining_phase')
let _inventoryBaseline = 0

let isMining = false
let _isPaused = false
let _targetCount = 0
let _loopGen = 0
let _currentGoal = {}  // shim: removed in combat.js step
const _digFailed = new Set()          // persists across loop restarts (inventory interruptions)
const _unavailablePickaxe = new Set() // persists across loop restarts
const _lavaOres = new Set()           // lava-adjacent ores — never cleared by tunnel success

const ORE_PRIORITY = [
    'diamond', 'emerald', 'ancient_debris',
    'gold', 'iron', 'lapis', 'coal',
]

const CLEARABLE_CLIMBABLES = new Set(['vine', 'weeping_vines', 'twisting_vines'])

// 不主動導航前往挖的礦石（在隧道路徑上遇到還是會挖）
const SKIP_ORES = new Set(['redstone', 'copper'])


const ORE_BEST_Y = {
    coal: 16, iron: 16, copper: 48,
    lapis: 0, gold: -16, redstone: -16,
    diamond: -58, emerald: 232,
    // raw_ / _ore / _ingot aliases
    raw_iron: 16, iron_ore: 16, deepslate_iron_ore: 16,
    raw_gold: -16, gold_ore: -16, deepslate_gold_ore: -16,
    raw_copper: 48, copper_ore: 48, deepslate_copper_ore: 48,
    coal_ore: 16, deepslate_coal_ore: 16,
    diamond_ore: -58, deepslate_diamond_ore: -58,
    lapis_ore: 0, deepslate_lapis_ore: 0,
}

// 挖各礦石所需最低稿子等級
const ORE_MIN_PICKAXE = {
    diamond:        'iron_pickaxe',
    emerald:        'iron_pickaxe',
    ancient_debris: 'diamond_pickaxe',
    gold:           'iron_pickaxe',
    iron:           'stone_pickaxe',
    redstone:       'iron_pickaxe',
    lapis:          'stone_pickaxe',
    copper:         'stone_pickaxe',
    // raw_ / _ore aliases
    raw_iron:             'stone_pickaxe',
    iron_ore:             'stone_pickaxe',
    deepslate_iron_ore:   'stone_pickaxe',
    raw_gold:             'iron_pickaxe',
    gold_ore:             'iron_pickaxe',
    deepslate_gold_ore:   'iron_pickaxe',
    diamond_ore:          'iron_pickaxe',
    deepslate_diamond_ore:'iron_pickaxe',
    // coal, copper, lapis, redstone, stone → 木稿即可，不需特別限制
}

const PICKAXE_TIERS = ['wooden_pickaxe', 'stone_pickaxe', 'iron_pickaxe', 'diamond_pickaxe', 'netherite_pickaxe']

function _hasPickaxeFor(bot, required) {
    return bot.inventory.items().some(i => PICKAXE_TIERS.indexOf(i.name) >= PICKAXE_TIERS.indexOf(required))
}

function _isLava(block) {
    return block && (block.name === 'lava' || block.name === 'flowing_lava')
}

function _isWaterBlock(block) {
    return block && (block.name === 'water' || block.name === 'flowing_water')
}

// 挖開 pos 後岩漿是否會流進來（檢查 pos 的 6 個鄰居）
function _hasAdjacentLava(bot, pos) {
    const offsets = [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]]
    return offsets.some(([dx, dy, dz]) => _isLava(bot.blockAt(pos.offset(dx, dy, dz))))
}

function _hasAdjacentWater(bot, pos) {
    const offsets = [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]]
    return offsets.some(([dx, dy, dz]) => _isWaterBlock(bot.blockAt(pos.offset(dx, dy, dz))))
}

// 嘗試用背包裡的方塊封堵岩漿（最佳努力，不保證成功）
async function _tryBlockLava(bot, lavaPos) {
    if (_shouldAbort()) return
    const filler = bot.inventory.items().find(i =>
        ['cobblestone', 'cobbled_deepslate', 'dirt', 'stone', 'gravel'].includes(i.name)
    )
    if (!filler) { console.log('[Mine] 沒有封堵材料，直接撤離'); return }

    await bot.equip(filler, 'hand').catch(() => {})

    const offsets = [[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]]
    for (const [dx, dy, dz] of offsets) {
        const ref = bot.blockAt(lavaPos.offset(dx, dy, dz))
        if (!ref || ref.boundingBox !== 'block') continue
        if (_isLava(ref)) continue
        if (!_isLava(bot.blockAt(lavaPos))) return  // 已被封
        try {
            await bot.lookAt(lavaPos.offset(0.5, 0.5, 0.5))
            if (_shouldAbort()) return
            await bot.placeBlock(ref, new Vec3(-dx, -dy, -dz))
            console.log('[Mine] 封堵岩漿成功')
            return
        } catch (_) {}
    }
    console.log('[Mine] 無法封堵岩漿')
}

// Accepts an ore block name or a mining target ("iron"); a missing name needs no tier.
function _requiredPickaxe(blockName) {
    if (!blockName) return 'wooden_pickaxe'
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

function _isNearWaterHazard(pos) {
    return hazards.isNear(pos, 'water', 6)
}

function _countItem(bot, name) {
    return bot.inventory.items()
        .filter(i => i.name === name)
        .reduce((sum, i) => sum + i.count, 0)
}

function _countBySuffix(bot, suffix) {
    return bot.inventory.items()
        .filter(i => i.name.endsWith(suffix))
        .reduce((sum, i) => sum + i.count, 0)
}

async function _equipToolForDig(bot, block) {
    if (!block || _shouldAbort()) return
    try {
        const equipped = await ensureToolFor(bot, block.name, false)
        if (equipped) return
    } catch (_) {}
    if (_shouldAbort()) return
    try {
        const tool = bot.pathfinder.bestHarvestTool(block)
        if (tool) await bot.equip(tool, 'hand')
    } catch (_) {}
}

function _buildNoToolsState(bot) {
    const logs = _countBySuffix(bot, '_log')
    const planks = _countBySuffix(bot, '_planks')
    const sticks = _countItem(bot, 'stick')
    const cobblestone = _countItem(bot, 'cobblestone')
    const ironIngot = _countItem(bot, 'iron_ingot')
    const diamond = _countItem(bot, 'diamond')
    const coal = _countItem(bot, 'coal') + _countItem(bot, 'charcoal')
    const effectivePlanks = planks + (logs * 4)
    const canMakeSticks = sticks >= 2 || effectivePlanks >= 2
    const canMakeStonePickaxe = cobblestone >= 3 && canMakeSticks
    const canMakeWoodPickaxe = effectivePlanks >= 5
    const canMakeIronPickaxe = ironIngot >= 3 && canMakeSticks
    const canMakeDiamondPickaxe = diamond >= 3 && canMakeSticks
    const canMakeAnyPickaxe = canMakeStonePickaxe || canMakeWoodPickaxe || canMakeIronPickaxe || canMakeDiamondPickaxe

    let detail = '沒有可用稿子，且工具準備失敗'
    let craftIssueSuspected = false

    if (canMakeAnyPickaxe) {
        detail = '背包中理論上有足夠材料可製作稿子，但 craft 流程仍失敗，疑似工具合成流程異常'
        craftIssueSuspected = true
    } else if (!canMakeSticks) {
        detail = '沒有可用稿子，且缺少足夠木材/木板來製作 sticks'
    } else if (cobblestone < 3 && ironIngot < 3 && diamond < 3 && effectivePlanks < 5) {
        detail = '沒有可用稿子，且缺少可做稿頭的材料（木板/圓石/鐵錠/鑽石）'
    } else if (coal <= 0 && ironIngot < 3 && diamond < 3 && cobblestone < 3 && effectivePlanks < 5) {
        detail = '沒有可用稿子，且缺少燃料與可直接合成稿子的材料'
    }

    return {
        detail,
        craft_issue_suspected: craftIssueSuspected,
        tool_state: {
            logs,
            planks,
            sticks,
            cobblestone,
            coal,
            iron_ingot: ironIngot,
            diamond,
            can_make_pickaxe_now: canMakeAnyPickaxe,
        },
    }
}

function _sendNoToolsStuck(bot) {
    bridge.sendState(bot, 'activity_stuck', {
        activity: 'mining',
        reason: 'no_tools',
        ..._buildNoToolsState(bot),
    })
}

// 嘗試合成稿子，若失敗才送 stuck。
// 回傳 true = 有稿子可繼續；false = 已送 stuck，呼叫方應 break。
async function _ensurePickaxeOrStuck(bot) {
    if (_currentGoal.target && !_hasPickaxe(bot)) {
        if (await _replaceBrokenPickaxe(bot, _requiredPickaxe(_currentGoal.target))) return true
        if (_shouldAbort()) return false
        isMining = false
        _sendNoToolsStuck(bot)
        return false
    }
    if (_hasPickaxe(bot)) return true
    console.log('[Mine] 無稿子，嘗試合成')
    // Crafting can legitimately take longer than the movement no-progress
    // deadline. Give the watchdog one bounded semantic wait; success produces
    // inventory evidence, while failure emits its own activity_stuck event.
    activityStack.waitFor('craft_pickaxe', 60000)
    try {
        await ensurePickaxe(bot)
    } finally {
        activityStack.endWait()
    }
    if (_hasPickaxe(bot)) {
        console.log('[Mine] 合成稿子成功，繼續')
        return true
    }
    console.log('[Mine] 合成稿子失敗，停止並請求決策')
    isMining = false
    _sendNoToolsStuck(bot)
    return false
}

async function _clearClimbablesAround(bot, positions) {
    const seen = new Set()
    for (const pos of positions) {
        const key = `${pos.x},${pos.y},${pos.z}`
        if (seen.has(key)) continue
        seen.add(key)
        const block = bot.blockAt(pos)
        if (!block || !CLEARABLE_CLIMBABLES.has(block.name)) continue
        try {
            await _equipToolForDig(bot, block)
            await _dig(bot, block)
            console.log(`[Mine] 清除 ${block.name} at (${pos.x}, ${pos.y}, ${pos.z})`)
            await _sleep(100)
        } catch (e) {
            console.log(`[Mine] 清除 ${block.name} 失敗: ${e.message}`)
        }
    }
}

function _setMovements(bot) {
    if (_shouldAbort()) return
    const movements = applyMovements(bot, { canDig: true })
    movements.maxDropDown = 8
}

function _setEscapeMovements(bot) {
    if (_shouldAbort()) return
    applyMovements(bot, {
        canDig: true,
        scaffoldBlockNames: ['cobbled_deepslate', 'cobblestone', 'dirt', 'stone', 'andesite', 'diorite', 'gravel', 'sand'],
    })
}

function _shouldAbort(expectedGen = null) {
    if (expectedGen < 0) expectedGen = null
    expectedGen ??= loopContext.getStore()
    return !isMining || (expectedGen !== undefined && expectedGen !== null && _loopGen !== expectedGen)
}

// A hazard escape (suffocation, lava, water) digs and moves on its own. Digging at the
// same time cancels its dig ("Digging aborted") and each side keeps undoing the other;
// a bot buried by falling gravel suffocated that way.
async function _waitForHazardEscape(expectedGen = null) {
    while (water.isEscaping() && !_shouldAbort(expectedGen)) await _sleep(200)
}

async function _dig(bot, block) {
    await _waitForHazardEscape()
    if (_shouldAbort()) throw new Error('mining aborted')
    const current = bot.blockAt(block.position)
    if (!current || current.name !== block.name) throw new Error(`block changed to ${current?.name ?? 'unloaded'}`)
    await bot.dig(current)
    if (_shouldAbort()) throw new Error('mining aborted')
}

function _hasPickaxe(bot) {
    return bot.inventory.items().some(i => i.name.endsWith('_pickaxe'))
}

// A named resource task only starts with an adequate pickaxe. If it breaks mid-task,
// replace it with the same tier from carried materials. This never gathers or smelts,
// so a real material shortage still yields no_tools for the planner.
async function _replaceBrokenPickaxe(bot, required) {
    console.log(`[Mine] 稿子已損壞，嘗試用背包材料補一把 ${required}`)
    activityStack.waitFor('craft_pickaxe', 60000)
    try {
        await ensurePickaxeTier(bot, required, true, true, false)
    } catch (_) {
    } finally {
        activityStack.endWait()
    }
    return _hasPickaxeFor(bot, required)
}

// Ores come in veins. Stopping the moment the count is reached left the rest of the vein
// in front of the bot, so also take same-target ore right next to it before reporting
// done. Bounded: nearby only, a few blocks, same safety skips as normal ore mining.
// Like all ore targeting it only uses exposed ore (no seeing through rock); digging one
// ore exposes its neighbours, so a connected vein is still followed.
const VEIN_RADIUS = 5
const VEIN_MAX_EXTRA = 8
const NO_VEIN_TARGETS = new Set(['stone', 'cobblestone', 'deepslate'])

async function _finishVein(bot, goal, gen) {
    if (NO_VEIN_TARGETS.has(goal.target)) return
    let extra = 0
    while (extra < VEIN_MAX_EXTRA && !_shouldAbort(gen)) {
        const orePos = bot.findBlocks({ matching: b => miningPhase.matches(b.name, goal.target), maxDistance: VEIN_RADIUS, count: 20 })
            .find(p => {
                const block = bot.blockAt(p)
                return block && _isExposed(bot, p) && !_digFailed.has(p.toString()) && !_lavaOres.has(p.toString()) &&
                    !_hasAdjacentLava(bot, p) && !_isNearWaterHazard(p) &&
                    _hasPickaxeFor(bot, _requiredPickaxe(block.name))
            })
        if (!orePos) return
        try {
            _setMovements(bot)
            await _goto(bot, new goals.GoalNear(orePos.x, orePos.y, orePos.z, 1), 8000)
            if (_shouldAbort(gen)) return
            const fresh = bot.blockAt(orePos)
            if (!fresh || !miningPhase.matches(fresh.name, goal.target)) { _digFailed.add(orePos.toString()); continue }
            await _equipToolForDig(bot, fresh)
            await _dig(bot, fresh)
            if (_shouldAbort(gen)) return
            extra++
            console.log(`[Mine] 順手挖完礦脈：${fresh.name}（額外 ${extra}）`)
            await _collectNearby(bot, orePos, 4)
        } catch (_) {
            if (_shouldAbort(gen)) return
            _digFailed.add(orePos.toString())
        }
    }
}

function _resetStaleMining(bot, reason = 'stale') {
    console.log(`[Mine] 偵測到殘留狀態，重置 mining (${reason})`)
    isMining = false
    _isPaused = false
    _loopGen++
    navigation.cancel(bot)
    _currentGoal = {}
    _digFailed.clear()
    _unavailablePickaxe.clear()
    _lavaOres.clear()
    try {
        bot.pathfinder?.setGoal(null)
    } catch (_) {}
    try {
        bot.clearControlStates?.()
    } catch (_) {}
    activityStack.forget('mining')
}

activityStack.register('mining', _pause)

function _pause(_bot) {
    if (_bot?.entity?.position) {
        activityStack.updateTopFrame({
            resumePos: {
                x: _bot.entity.position.x,
                y: _bot.entity.position.y,
                z: _bot.entity.position.z,
            },
        })
    }
    isMining = false
    _isPaused = true
    _loopGen++ // In-flight helpers must not become active again after resume.
    navigation.cancel(_bot)
    try { _bot.pathfinder?.setGoal(null) } catch (_) {}
    try { _bot.stopDigging?.() } catch (_) {}
    console.log('[Mine] 暫停挖礦')
}

async function startMining(bot, goal = {}) {
    if (isMining) {
        if (activityStack.isStale('mining', 15000)) {
            _resetStaleMining(bot, 'start_guard')
        } else {
        console.log('[Mine] 已在挖礦中')
        return
        }
    }
    if (goal.count !== undefined && !Number.isFinite(goal.count)) delete goal.count
    _currentGoal = goal
    _targetCount = 0
    _inventoryBaseline = miningPhase.count(bot, goal.target)
    _digFailed.clear()
    _unavailablePickaxe.clear()
    _lavaOres.clear()
    isMining = true
    activityStack.push(bot, 'mining', goal, (b) => _resumeMining(b, goal))
    activityStack.markStarted('mining', 'start')
    console.log('[Mine] 開始挖礦')
    _loop(bot, goal)
}

function _resumeMining(bot, originalGoal) {
    if (isMining) return
    const remaining = originalGoal.count
        ? Math.max(1, originalGoal.count - _targetCount)
        : undefined
    const top = activityStack.getTopFrame()
    // Resume the resource goal, not an obsolete route to the interruption site.
    const resumePos = !navigation.enabled() && top?.activity === 'mining' ? top.resumePos : null
    isMining = true
    activityStack.markStarted('mining', 'resume')
    activityStack.updateTopGoal(remaining ? { ...originalGoal, count: remaining } : originalGoal)
    console.log('[Mine] 恢復挖礦')
    _loop(bot, originalGoal, resumePos)
}

function stopMining(bot) {
    if (!isMining && !activityStack.has('mining')) return
    isMining = false
    _isPaused = false
    _loopGen++  // invalidate current loop so it skips its own pop
    navigation.cancel(bot)
    try { bot.stopDigging?.() } catch (_) {}
    _currentGoal = {}
    _digFailed.clear()
    _unavailablePickaxe.clear()
    _lavaOres.clear()
    try {
        bot.pathfinder?.setGoal(null)
    } catch (_) {}
    try {
        bot.clearControlStates?.()
    } catch (_) {}
    activityStack.markStopped('mining', 'stop')
    console.log('[Mine] 停止挖礦')
    activityStack.remove(bot, 'mining', { resumePrevious: false })
}

function _loop(bot, goal = {}, resumePos = null) {
    const _myGen = ++_loopGen
    return loopContext.run(_myGen, async () => {
        try {
            await _runLoop(bot, goal, resumePos, _myGen)
        } catch (e) {
            if (_shouldAbort(_myGen)) return
            isMining = false
            bridge.sendState(bot, 'activity_stuck', {
                activity: 'mining', reason: 'no_blocks', detail: e.message,
                navigation: navigation.lastResult(bot),
            })
            if (activityStack.isTopActivity('mining')) activityStack.pop(bot)
        }
    })
}

async function _runLoop(bot, goal, resumePos, _myGen) {
    _isPaused = false
    const startTime = Date.now()
    let tunnelYaw = bot.entity.yaw
    let wallNavAttempts = 0
    const bestY = goal.target ? (ORE_BEST_Y[goal.target] ?? null) : null
    let descentRotations = 0
    let inReachLogged = false
    let descentHardFails = 0
    let tunnelFailCount = 0
    let stoneSearchFails = 0
    let stoneRotations = 0
    const before = miningPhase.snapshot(bot, goal.target)
    let phase = 'prepare'
    const yieldDecision = (reason, extra = {}) => {
        isMining = false
        bridge.sendState(bot, 'mining_decision', {
            activity: 'mining', reason,
            mining_result: { ...miningPhase.result(bot, goal, before, phase, reason),
                observations: { lava_adjacent_ores: [..._lavaOres].slice(-8),
                    failed_targets: [..._digFailed].slice(-8),
                    missing_tools: [..._unavailablePickaxe] }, ...extra },
        })
    }
    // A named resource task does not secretly craft or pursue prerequisite ores.
    const required = _requiredPickaxe(goal.target)
    if (goal.target && !_hasPickaxeFor(bot, required)) {
        yieldDecision('no_tools', { required_tool: required, ..._buildNoToolsState(bot) })
        if (activityStack.isTopActivity('mining')) activityStack.pop(bot)
        return
    }

    // 每次進入 loop（包含 resume 後）先嘗試合成稿子
    if (!_hasPickaxe(bot)) {
        const ok = await _ensurePickaxeOrStuck(bot)
        if (!ok || _shouldAbort(_myGen)) return
    }

    if (resumePos && _shouldReturnToResumePos(bot, resumePos)) {
        console.log(`[Mine] 返回中斷前的礦點 (${Math.floor(resumePos.x)}, ${Math.floor(resumePos.y)}, ${Math.floor(resumePos.z)})`)
        try {
            _setMovements(bot)
            await _goto(bot, new goals.GoalNear(
                Math.floor(resumePos.x),
                Math.floor(resumePos.y),
                Math.floor(resumePos.z),
                2,
            ), 20000)
            if (_shouldAbort(_myGen)) return
            activityStack.updateTopFrame({
                resumePos: {
                    x: bot.entity.position.x,
                    y: bot.entity.position.y,
                    z: bot.entity.position.z,
                },
            })
            activityStack.touch('mining', 'resume_position')
        } catch (e) {
            console.log(`[Mine] 無法回到中斷前礦點，改從目前位置繼續: ${e.message}`)
        }
    }

    while (!_shouldAbort(_myGen)) {
        if (goal.target && !_hasPickaxeFor(bot, required)) {
            const replaced = await _replaceBrokenPickaxe(bot, required)
            if (_shouldAbort(_myGen)) return
            if (!replaced) {
                yieldDecision('no_tools', { required_tool: required })
                break
            }
        }
        _targetCount = Math.max(0, miningPhase.count(bot, goal.target) - _inventoryBaseline)
        const nextPhase = bestY !== null && bot.entity.position.y > bestY + 1 ? 'descending' : 'collecting'
        activityStack.updateProgress({ count: _targetCount, phase: nextPhase, target_y: bestY })
        if (nextPhase !== phase) {
            phase = nextPhase
            bridge.sendState(bot, 'mining_progress', {
                activity: 'mining', phase, target: goal.target, acquired: _targetCount,
            })
        }
        _rememberResumePos(bot)
        if (eating.isEating()) {
            await _sleep(250)
            continue
        }

        // 每輪檢查：若之前因材料不足跳過某等級，看背包現在是否已有足夠材料可解除
        if (_unavailablePickaxe.size > 0) {
            const ironIngots = bot.inventory.items().filter(i => i.name === 'iron_ingot').reduce((s, i) => s + i.count, 0)
            const rawIron    = bot.inventory.items().some(i => ['raw_iron','iron_ore','deepslate_iron_ore'].includes(i.name))
            const hasFurnace = !!bot.findBlock({ matching: b => ['furnace','lit_furnace','blast_furnace'].map(n => bot.registry.blocksByName[n]?.id).filter(Boolean).includes(b.type), maxDistance: 32 })
            const cobble     = bot.inventory.items().filter(i => i.name === 'cobblestone').reduce((s, i) => s + i.count, 0)
            if (ironIngots >= 3 || (rawIron && (hasFurnace || cobble >= 8))) {
                console.log('[Mine] 已有足夠鐵礦/鐵錠，解除稿子限制')
                _unavailablePickaxe.clear()
            }
        }

        // 停止條件
        if (goal.duration && Date.now() - startTime >= goal.duration * 1000) {
            console.log(`[Mine] 達到時間目標 ${goal.duration}s，停止`)
            isMining = false
            sendActivityDone(bot, 'mining', { goal_target: goal.target ?? 'general', mined_pos: bot.entity.position })
            break
        }
        if (goal.target && goal.count && _targetCount >= goal.count) {
            await _finishVein(bot, goal, _myGen)
            if (_shouldAbort(_myGen)) return
            console.log(`[Mine] 達到目標 ${goal.target} x${goal.count}，停止`)
            isMining = false
            sendActivityDone(bot, 'mining', { goal_target: goal.target ?? 'general', mined_pos: bot.entity.position })
            break
        }

        if (goal.target === 'stone') {
            const nearbyStone = bot.findBlocks({
                matching: b => ['stone', 'cobblestone'].includes(b.name),
                maxDistance: 6,
                count: 20,
            })
                .filter(p => _isExposed(bot, p) && !_digFailed.has(p.toString()) && !_isNearWaterHazard(p))
                .sort((a, b) => a.distanceTo(bot.entity.position) - b.distanceTo(bot.entity.position))

            if (nearbyStone.length > 0) {
                const pos = nearbyStone[0]
                const block = bot.blockAt(pos)
                if (!block) continue
                console.log(`[Mine] 目標 ${block.name} at y=${pos.y}`)
                try {
                    await _goto(bot, new goals.GoalNear(pos.x, pos.y, pos.z, 1), 8000)
                    if (_shouldAbort(_myGen)) return
                    const fresh = bot.blockAt(pos)
                    if (!fresh || !['stone', 'cobblestone'].includes(fresh.name)) {
                        _digFailed.add(pos.toString())
                        continue
                    }
                    activityStack.touch('mining', 'stone_target')
                    const ok = await ensureToolFor(bot, 'stone', false)
                    if (!ok) {
                        console.log('[Mine] 無法取得稿子，停止並請求決策')
                        isMining = false
                        _sendNoToolsStuck(bot)
                        break
                    }
                    await _dig(bot, fresh)
                    if (_shouldAbort(_myGen)) return
                    _digFailed.delete(pos.toString())
                    _targetCount++
                    activityStack.touch('mining', 'dug_block')
                    activityStack.updateProgress({ count: _targetCount })
                    stoneSearchFails = 0
                    console.log(`[Mine] 挖下 ${fresh.name} (目標 ${_targetCount}/${goal.count})`)
                    await _sleep(300)
                    if (_shouldAbort(_myGen)) return
                    await _collectNearby(bot, pos, 4)
                    if (_shouldAbort(_myGen)) return
                } catch (e) {
                    if (_shouldAbort(_myGen)) return
                    console.log(`[Mine] 挖石頭失敗: ${e.message}`)
                    _digFailed.add(pos.toString())
                    stoneSearchFails++
                }
                continue
            }

            const beforeY = Math.floor(bot.entity.position.y)
            if (!await _ensurePickaxeOrStuck(bot)) break
            if (_shouldAbort(_myGen)) return
            console.log('[Mine] 附近沒有石頭，往下潛尋找')
            await _stepDown(bot, beforeY - 3, tunnelYaw)
            if (_shouldAbort(_myGen)) return
            const afterY = Math.floor(bot.entity.position.y)
            if (afterY >= beforeY) {
                stoneSearchFails++
                if (stoneSearchFails >= 2) {
                    tunnelYaw += Math.PI / 2
                    stoneSearchFails = 0
                    stoneRotations++
                    console.log('[Mine] 下潛未成功，旋轉 90° 繼續找石頭')
                    if (stoneRotations >= 4) {
                        stoneRotations = 0
                        if (!await _ensurePickaxeOrStuck(bot)) break
                        if (_shouldAbort(_myGen)) return
                        console.log('[Mine] 四方無石頭，強制往下直挖至 Y=40')
                        await _digStraightDown(bot, 40)
                        if (_shouldAbort(_myGen)) return
                    }
                }
            } else {
                stoneSearchFails = 0
                stoneRotations = 0
            }
            continue
        }

        if (goal.target === 'cobblestone') {
            const nearbyStone = bot.findBlocks({
                matching: b => ['stone', 'cobblestone'].includes(b.name),
                maxDistance: 6,
                count: 20,
            })
                .filter(p => _isExposed(bot, p) && !_digFailed.has(p.toString()) && !_isNearWaterHazard(p))
                .sort((a, b) => a.distanceTo(bot.entity.position) - b.distanceTo(bot.entity.position))

            if (nearbyStone.length > 0) {
                const pos = nearbyStone[0]
                const block = bot.blockAt(pos)
                if (!block) continue
                console.log(`[Mine] cobblestone 目標 ${block.name} at y=${pos.y}`)
                try {
                    await _goto(bot, new goals.GoalNear(pos.x, pos.y, pos.z, 1), 8000)
                    if (_shouldAbort(_myGen)) return
                    const fresh = bot.blockAt(pos)
                    if (!fresh || !['stone', 'cobblestone'].includes(fresh.name)) {
                        _digFailed.add(pos.toString())
                        continue
                    }
                    activityStack.touch('mining', 'stone_target')
                    const ok = await ensureToolFor(bot, 'stone', false)
                    if (!ok) {
                        isMining = false
                        _sendNoToolsStuck(bot)
                        break
                    }
                    await _dig(bot, fresh)
                    if (_shouldAbort(_myGen)) return
                    _digFailed.delete(pos.toString())
                    _targetCount++
                    activityStack.touch('mining', 'dug_block')
                    activityStack.updateProgress({ count: _targetCount })
                    console.log(`[Mine] 挖下 ${fresh.name} (目標 ${_targetCount}/${goal.count})`)
                    await _sleep(300)
                    if (_shouldAbort(_myGen)) return
                    await _collectNearby(bot, pos, 4)
                    if (_shouldAbort(_myGen)) return
                } catch (e) {
                    if (_shouldAbort(_myGen)) return
                    console.log(`[Mine] cobblestone 挖石失敗: ${e.message}`)
                    _digFailed.add(pos.toString())
                }
                continue
            }

            // 附近沒石頭，往下直挖到 Y=60
            const cobbleCurrentY = Math.floor(bot.entity.position.y)
            if (cobbleCurrentY > 40) {
                if (!await _ensurePickaxeOrStuck(bot)) break
                if (_shouldAbort(_myGen)) return
                console.log(`[Mine] cobblestone: 直挖往下 Y=${cobbleCurrentY} → 40`)
                await _digStraightDown(bot, 40)
                if (_shouldAbort(_myGen)) return
            }
            continue
        }

        const currentY = Math.floor(bot.entity.position.y)
        const needDescend = bestY !== null && currentY - bestY > 3
        // Reachable target ore at this depth beats climbing to the statistical best
        // height (e.g. deepslate iron at y=-35 vs the iron peak at y=16).
        const targetInReach = goal.target && bestY !== null && bestY - currentY > 3 &&
            bot.findBlocks({ matching: b => miningPhase.matches(b.name, goal.target), maxDistance: 16, count: 20 })
                .some(p => _isExposed(bot, p) && !_digFailed.has(p.toString()) && !_lavaOres.has(p.toString()))
        if (targetInReach && !inReachLogged) console.log(`[Mine] 目前深度 Y=${currentY} 附近已有可挖的 ${goal.target}，不先回到 Y=${bestY}`)
        inReachLogged = Boolean(targetInReach)
        const needAscend  = bestY !== null && bestY - currentY > 3 && !targetInReach

        if (needAscend) {
            console.log(`[Mine] 位置過深 Y=${currentY}，回到目標高度 Y=${bestY}`)
            _setEscapeMovements(bot)
            try {
                await _goto(bot, new goals.GoalNear(
                    Math.floor(bot.entity.position.x), bestY,
                    Math.floor(bot.entity.position.z), 3
                ), 20000)
                if (_shouldAbort(_myGen)) return
            } catch (e) {
                console.log('[Mine] 無法上升到目標高度:', e.message)
            }
            _setMovements(bot)
            if (Math.floor(bot.entity.position.y) < bestY - 3) {
                console.log('[Mine] 上升失敗（仍遠離目標高度），向上挖掘逃脫...')
                await _digEscape(bot, bestY)
                if (_shouldAbort(_myGen)) return
                isMining = false
                bridge.sendState(bot, 'activity_stuck', { activity: 'mining', reason: 'no_blocks' })
                break
            }
        } else if (needDescend) {
            // 等待水/岩漿逃脫完成再下潛
            if (water.isEscaping()) { await _sleep(500); continue }
            if (!await _ensurePickaxeOrStuck(bot)) break
            if (_shouldAbort(_myGen)) return
            // 主動作：挖階梯往下
            const descended = await _stepDown(bot, bestY, tunnelYaw)
            if (_shouldAbort(_myGen)) return

            // Each path attempt already has a five-second deadline. Rotate after
            // one failed direction so all four local alternatives fit inside the
            // watchdog budget instead of retrying one blocked cell three times.
            if (!descended) {
                tunnelYaw += Math.PI / 2
                descentRotations++
                console.log('[Mine] 下潛方向受阻，旋轉 90° 繼續')
                if (descentRotations >= 4) {
                    descentRotations = 0
                    if (descentHardFails >= 2) {
                        console.log('[Mine] 多次無法下潛，停止')
                        yieldDecision('no_safe_route', { attempts: { descent_rotations: 4, escape_attempts: descentHardFails }, navigation: navigation.lastResult(bot) })
                        break
                    }
                    const digY = Math.min(2, Math.floor(bot.entity.position.y) - bestY)
                    console.log(`[Mine] 四方受阻，嘗試往下直挖 ${digY} 格`)
                    await _digEscape(bot, Math.floor(bot.entity.position.y) - digY)
                    if (_shouldAbort(_myGen)) return
                    descentHardFails++
                }
            } else {
                descentRotations = 0
                descentHardFails = 0
            }

            // 順手：挖階梯時旁邊看到的礦（8 格內）
            const nearbyOres = bot.findBlocks({ matching: b => miningPhase.matches(b.name, goal.target), maxDistance: 8, count: 20 })
                .filter(p => _isExposed(bot, p))
                .sort((a, b) => _priority(bot.blockAt(a)?.name) - _priority(bot.blockAt(b)?.name))

            for (const orePos of nearbyOres) {
                if (_shouldAbort()) return
                if (eating.isEating()) {
                    await _sleep(250)
                    continue
                }
                const block = bot.blockAt(orePos)
                if (!block || !block.name.endsWith('_ore')) continue

                _setMovements(bot)
                try {
                    await _goto(bot, new goals.GoalNear(orePos.x, orePos.y, orePos.z, 1), 12000)
                    if (_shouldAbort(_myGen)) return
                    const fresh = bot.blockAt(orePos)
                    if (!fresh || !fresh.name.endsWith('_ore')) continue
                    if (_hasAdjacentLava(bot, fresh.position)) {
                        console.log(`[Mine] 礦石 ${fresh.name} 鄰近岩漿，跳過`)
                        _lavaOres.add(fresh.position.toString())
                        continue
                    }
                    const required = _requiredPickaxe(fresh.name)
                    const ok = await ensurePickaxeTier(bot, required, false, !goal.target)
                    if (_shouldAbort(_myGen)) return
                    if (!ok) continue
                    await _dig(bot, fresh)
                    if (_shouldAbort(_myGen)) return
                    const isTarget = goal.target && fresh.name.includes(goal.target)
                    if (isTarget) {
                        _targetCount++
                        activityStack.updateProgress({ count: _targetCount })
                    }
                    console.log(`[Mine] 挖下 ${fresh.name}${isTarget ? ` (目標 ${_targetCount}/${goal.count})` : ''}`)
                    await _sleep(300)
                    if (_shouldAbort(_myGen)) return
                    await _collectNearby(bot, orePos, 4)
                    if (_shouldAbort(_myGen)) return
                } catch (_) {}
            }

        } else if (!needAscend) {
            // 已到目標深度：找附近礦石挖（嚴格限制在 bestY ±1），沒有就挖隧道
            const allExposed = bot.findBlocks({ matching: b => miningPhase.matches(b.name, goal.target), maxDistance: 16, count: 50 })
                .filter(p => {
                    if (!_isExposed(bot, p)) return false
                    const blockName = bot.blockAt(p)?.name
                    const isTargetOre = goal.target && blockName?.includes(goal.target)
                    if (!isTargetOre && bestY !== null && Math.abs(p.y - bestY) > 5) return false
                    if (_digFailed.has(p.toString())) return false
                    if (_lavaOres.has(p.toString())) return false
                    if (_isNearWaterHazard(p)) return false
                    if (_unavailablePickaxe.size > 0) {
                        const req = _requiredPickaxe(bot.blockAt(p)?.name)
                        if (_unavailablePickaxe.has(req)) return false
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
                if (eating.isEating()) {
                    await _sleep(250)
                    continue
                }

                if (_hasAdjacentLava(bot, pos)) {
                    console.log(`[Mine] 礦石 ${block.name} 鄰近岩漿，跳過`)
                    _lavaOres.add(pos.toString())
                    continue
                }
                console.log(`[Mine] 目標 ${block.name} at y=${pos.y}`)
                _setMovements(bot)
                try {
                    await _goto(bot, new goals.GoalNear(pos.x, pos.y, pos.z, 1), 12000)
                } catch (e) {
                    if (_shouldAbort(_myGen)) return
                    if (e.message.includes('goal was changed') || e.message.includes('goal changed')) {
                        while (water.isEscaping() && !_shouldAbort()) await _sleep(300)
                        await _sleep(200)
                        try {
                            await _goto(bot, new goals.GoalNear(pos.x, pos.y, pos.z, 1), 12000)
                            if (_shouldAbort(_myGen)) return
                        } catch (e2) {
                            console.log(`[Mine] 無法導航到礦石: ${e2.message}`)
                            _digFailed.add(pos.toString())
                            continue
                        }
                    } else {
                        console.log(`[Mine] 無法導航到礦石: ${e.message}`)
                        _digFailed.add(pos.toString())
                        continue
                    }
                }

                if (_shouldAbort(_myGen)) return

                const fresh = bot.blockAt(pos)
                if (!fresh || !fresh.name.endsWith('_ore')) continue

                try {
                    const required = _requiredPickaxe(fresh.name)
                    const ok = await ensurePickaxeTier(bot, required, false, !goal.target)
                    if (_shouldAbort(_myGen)) return
                    if (!ok) {
                        console.log(`[Mine] 材料不足無法取得 ${required}，跳過需要它的礦`)
                        _unavailablePickaxe.add(required)
                        _digFailed.add(pos.toString())
                        continue
                    }
                    _unavailablePickaxe.delete(required)  // 成功取得，解除跳過
                    if (_hasAdjacentLava(bot, fresh.position)) {
                        console.log(`[Mine] 礦石 ${fresh.name} 鄰近岩漿，跳過`)
                        _lavaOres.add(pos.toString())
                        continue
                    }
                    await _dig(bot, fresh)
                    if (_shouldAbort(_myGen)) return
                    _digFailed.delete(pos.toString())
                    const isTarget = goal.target && fresh.name.includes(goal.target)
                    if (isTarget) {
                        _targetCount++
                        activityStack.updateProgress({ count: _targetCount })
                    }
                    console.log(`[Mine] 挖下 ${fresh.name}${isTarget ? ` (目標 ${_targetCount}/${goal.count})` : ''}`)
                    await _sleep(300)
                    if (_shouldAbort(_myGen)) return
                    await _collectNearby(bot, pos, 4)
                    if (_shouldAbort(_myGen)) return
                } catch (e) {
                    console.log('[Mine] 挖掘失敗:', e.message)
                    _digFailed.add(pos.toString())
                    await _sleep(300)
                }

            } else {
                // 先嘗試沿洞穴導航到更遠的礦石（不限 Y），讓 pathfinder 自然走進洞穴
                const detectedOres = bot.findBlocks({ matching: b => miningPhase.matches(b.name, goal.target), maxDistance: 32, count: 20 })
                const wideOres = detectedOres
                    .filter(p => _isExposed(bot, p))
                    .filter(p => !_isNearWaterHazard(p))
                    .sort((a, b) => _priority(bot.blockAt(a)?.name) - _priority(bot.blockAt(b)?.name))
                if (wideOres.length > 0) {
                    const wp = wideOres[0]
                    if (_digFailed.has(wp.toString())) {
                        // 已知無法挖，跳過直接挖隧道
                    } else {
                        console.log(`[Mine] 廣域搜尋到 ${bot.blockAt(wp)?.name} at y=${wp.y}，嘗試導航`)
                        _setMovements(bot)
                        try {
                            await _goto(bot, new goals.GoalNear(wp.x, wp.y, wp.z, 1), 12000)
                            tunnelFailCount = 0
                        } catch (_) {
                            console.log('[Mine] 廣域導航失敗，改挖隧道')
                            _digFailed.add(wp.toString())
                            continue
                        }
                        // 導航成功，嘗試挖礦
                        const freshWide = bot.blockAt(wp)
                        if (freshWide && freshWide.name.endsWith('_ore') && !_hasAdjacentLava(bot, freshWide.position)) {
                            try {
                                const required = _requiredPickaxe(freshWide.name)
                                const ready = await ensurePickaxeTier(bot, required, false, !goal.target)
                                if (!ready) { _unavailablePickaxe.add(required); _digFailed.add(wp.toString()); continue }
                                if (_shouldAbort(_myGen)) return
                                await _dig(bot, freshWide)
                                if (_shouldAbort(_myGen)) return
                                _digFailed.delete(wp.toString())
                                const isTarget = goal.target && freshWide.name.includes(goal.target)
                                if (isTarget) {
                                    _targetCount++
                                    activityStack.updateProgress({ count: _targetCount })
                                }
                                console.log(`[Mine] 挖下廣域 ${freshWide.name}`)
                            } catch (e) {
                                console.log(`[Mine] 廣域挖掘失敗: ${e.message}`)
                                _digFailed.add(wp.toString())
                            }
                        } else {
                            _digFailed.add(wp.toString())
                        }
                        continue
                    }
                }
                // 所有廣域礦石都因缺工具被跳過 → 主動通知 Python 補工具
                const allToolBlocked = wideOres.length > 0
                    && wideOres.every(p => _digFailed.has(p.toString()))
                    && _unavailablePickaxe.size > 0
                if (allToolBlocked) {
                    console.log('[Mine] 附近礦石全部因缺工具被跳過，通知 Python 補工具')
                    isMining = false
                    bridge.sendState(bot, 'activity_stuck', {
                        activity: 'mining',
                        reason: 'no_tools',
                        detail: `需要 ${[..._unavailablePickaxe].join('/')} 才能繼續挖礦`,
                        suggested_actions: ['chop', 'mine', 'home'],
                    })
                    break
                }
                // Only exposed ore counts as seen; buried ore is not reported (no x-ray).
                console.log(wideOres.length
                    ? `[Mine] 看得到 ${wideOres.length} 個目標礦石但無法前往（其中先前失敗=${wideOres.filter(p => _digFailed.has(p.toString())).length}），改挖隧道`
                    : '[Mine] 附近沒有看得到的目標礦石，挖隧道繼續')
                if (!await _ensurePickaxeOrStuck(bot)) break
                if (_shouldAbort(_myGen)) return
                const tunneled = await _digTunnel(bot, tunnelYaw, 16, bestY, goal, _myGen)
                if (_shouldAbort(_myGen)) return
                if (!tunneled) {
                    if (!await _ensurePickaxeOrStuck(bot)) break
                    if (_shouldAbort(_myGen)) return
                    tunnelFailCount++
                    tunnelYaw += Math.PI / 2
                    console.log(`[Mine] 隧道受阻，旋轉方向繼續 (${tunnelFailCount}/4)`)
                    if (tunnelFailCount >= 4) {
                        if (_canMoveHorizontally(bot)) {
                            // 開放空間：找最近的牆壁走過去，疊方塊也可以
                            const wall = bot.findBlock({
                                matching: b => b.boundingBox === 'block' && b.hardness >= 0
                                    && b.position && !b.name.includes('bedrock') && !isBuried(b.position),
                                maxDistance: 32,
                            })
                            if (wall && wallNavAttempts < 3) {
                                wallNavAttempts++
                                console.log(`[Mine] 開放空間，疊方塊導航到牆壁 ${wall.name} at ${wall.position}`)
                                _setEscapeMovements(bot)
                                let reached = false
                                try {
                                    await _goto(bot, new goals.GoalNear(wall.position.x, wall.position.y, wall.position.z, 2), 12000)
                                    reached = true
                                } catch (_) {}
                                _setMovements(bot)
                                if (reached) {
                                    const dx = wall.position.x - bot.entity.position.x
                                    const dz = wall.position.z - bot.entity.position.z
                                    tunnelYaw = Math.atan2(-dx, -dz)
                                    tunnelFailCount = 0
                                    wallNavAttempts = 0
                                    continue
                                }
                            }
                            // 走不到任何牆壁，往 tunnelYaw 方向強制移動探索
                            console.log('[Mine] 無法到達牆壁，往前強制探索...')
                            _setEscapeMovements(bot)
                            const edx = Math.round(-Math.sin(tunnelYaw))
                            const edz = Math.round(-Math.cos(tunnelYaw))
                            try {
                                await _goto(bot, new goals.GoalNear(
                                    Math.floor(bot.entity.position.x) + edx * 10,
                                    Math.floor(bot.entity.position.y),
                                    Math.floor(bot.entity.position.z) + edz * 10,
                                    3
                                ), 12000)
                            } catch (_) {}
                            _setMovements(bot)
                            tunnelYaw += Math.PI / 2
                            tunnelFailCount = 0
                            wallNavAttempts = 0
                            continue
                        }
                        // 真的被困（四周都是實心塊）：若在 bestY 以下先疊上去
                        if (bestY !== null && Math.floor(bot.entity.position.y) < bestY) {
                            console.log('[Mine] 低於目標高度且四周受阻，疊回 bestY...')
                            await _digEscape(bot, bestY)
                            if (_shouldAbort(_myGen)) return
                            tunnelFailCount = 0
                            tunnelYaw = bot.entity.yaw
                            continue
                        }
                        console.log('[Mine] 四個方向都無法繼續，向上挖掘逃脫...')
                        await _digEscape(bot, Math.floor(bot.entity.position.y) + 20)
                        if (_shouldAbort(_myGen)) return
                        // if we escaped to open space, reset and retry mining
                        if (_canMoveHorizontally(bot)) {
                            console.log('[Mine] 逃脫成功，重試挖礦')
                            tunnelFailCount = 0
                            tunnelYaw = bot.entity.yaw
                            continue
                        }
                        isMining = false
                        bridge.sendState(bot, 'activity_stuck', { activity: 'mining', reason: 'no_blocks' })
                        break
                    }
                } else {
                    tunnelFailCount = 0
                }
            }
        }
    }

    if (_loopGen === _myGen) {
        if (!_isPaused) activityStack.pop(bot)
        _isPaused = false
    }
}

function _rememberResumePos(bot) {
    if (!bot?.entity?.position) return
    activityStack.updateTopFrame({
        resumePos: {
            x: bot.entity.position.x,
            y: bot.entity.position.y,
            z: bot.entity.position.z,
        },
    })
}

function _shouldReturnToResumePos(bot, resumePos) {
    if (!bot?.entity?.position || !resumePos) return false
    const current = bot.entity.position
    const dy = Math.abs(current.y - resumePos.y)
    const dx = current.x - resumePos.x
    const dz = current.z - resumePos.z
    const horizontal = Math.sqrt((dx * dx) + (dz * dz))
    return dy > 6 || horizontal > 8
}

// 往目標 Y 走一步（pathfinder 自動挖出階梯）
// 手動挖斜梯：每次「前進1格 + 往下1格」，重複 steps 次
// The next stair lands one block down; the block under that spot must be solid. Air
// there means a cave or ravine: a live run stepped in at y=-1 and fell 17 blocks to death.
function _hasSolidFloor(bot, pos) {
    const block = bot.blockAt(pos)
    return !!block && block.boundingBox === 'block' && !_isLava(block)
}

// Non-falling blocks a stair landing may be built from (gravel/sand would drop into the hole).
const FLOOR_BLOCKS = ['cobbled_deepslate', 'cobblestone', 'dirt', 'stone', 'deepslate', 'tuff',
    'andesite', 'diorite', 'granite', 'netherrack']

// Seen live: above a cave every stair direction had no floor and the descent gave up while
// carrying 128 building blocks. Place one under the landing instead, as a player would.
async function _placeFloor(bot, pos) {
    const current = bot.blockAt(pos)
    if (!current || _isLava(current) || _hasAdjacentLava(bot, pos)) return false
    const filler = bot.inventory.items().find(i => FLOOR_BLOCKS.includes(i.name))
    if (!filler) return false
    for (const [dx, dy, dz] of [[0, 1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1], [0, -1, 0]]) {
        const ref = bot.blockAt(pos.offset(dx, dy, dz))
        if (!ref || ref.boundingBox !== 'block' || _isLava(ref)) continue
        try {
            await bot.equip(filler, 'hand')
            if (_shouldAbort()) return false
            await bot.placeBlock(ref, new Vec3(-dx, -dy, -dz))
        } catch (_) { continue }
        if (_hasSolidFloor(bot, pos)) {
            console.log(`[Mine] 下潛落點下方是空洞，放 ${filler.name} 當地板`)
            return true
        }
    }
    return false
}

async function _stairDown(bot, yaw, steps) {
    const dx = Math.round(-Math.sin(yaw))
    const dz = Math.round(-Math.cos(yaw))

    for (let i = 0; i < steps; i++) {
        if (_shouldAbort()) return
        if (water.isEscaping()) return
        if (eating.isEating()) {
            await _sleep(250)
            continue
        }

        const feet = bot.entity.position.floored()

        // 先 check 三格岩漿（頭、腳、落點地板）再動手
        const _stairPick = bot.inventory.items().find(i => i.name.endsWith('_pickaxe'))
        if (!_stairPick) return  // 無稿子，無法下潛
        try { await bot.equip(_stairPick, 'hand') } catch (_) {}
        let lavaDetected = false
        for (const off of [[dx, 1, dz], [dx, 0, dz], [dx, -1, dz]]) {
            const b = bot.blockAt(feet.offset(...off))
            if (!b) continue
            if (_isLava(b) || _hasAdjacentLava(bot, b.position)) {
                console.log('[Mine] 前方偵測到岩漿，中止下潛')
                lavaDetected = true
                break
            }
        }
        if (lavaDetected) return
        const floorPos = feet.offset(dx, -2, dz)
        if (!_hasSolidFloor(bot, floorPos) && !(await _placeFloor(bot, floorPos))) {
            console.log('[Mine] 下潛落點下方是空洞（洞穴/峽谷）且無法補地板，不往這個方向下')
            return
        }

        await _clearClimbablesAround(bot, [
            feet.offset(dx, 1, dz),
            feet.offset(dx, 0, dz),
            feet.offset(dx, -1, dz),
            feet.offset(dx, 2, dz),
        ])

        // 先挖三格（頭、腳、地板），再往前走自然落下
        for (const off of [[dx, 1, dz], [dx, 0, dz], [dx, -1, dz]]) {
            const b = bot.blockAt(feet.offset(...off))
            if (!b || b.boundingBox !== 'block') continue
            try { await _equipToolForDig(bot, b); await _dig(bot, b) } catch (_) {}
        }

        // 走到前方低一格的位置（pathfinder 會自然掉落）
        _setMovements(bot)
        try {
            await _goto(bot, new goals.GoalBlock(feet.x + dx, feet.y - 1, feet.z + dz), 5000)
        } catch (_) { break }
        await _sleep(300)  // 等落地
    }
}

async function _stepDown(bot, targetY, yaw) {
    if (_shouldAbort()) return false
    const pick = bot.inventory.items().find(i => i.name.endsWith('_pickaxe'))
    if (!pick) {
        console.log('[Mine] 沒有稿子，無法下潛')
        return false
    }
    try { await bot.equip(pick, 'hand') } catch (_) {}
    const currentY = Math.floor(bot.entity.position.y)
    const steps = Math.min(3, currentY - targetY)
    if (steps <= 0) return false
    console.log(`[Mine] 下潛斜梯 ${steps} 格 → Y=${currentY - steps}`)
    await _stairDown(bot, yaw, steps)
    const descended = Math.floor(bot.entity.position.y) < currentY
    if (descended) activityStack.touch('mining', 'descending')
    return descended
}

async function _digStraightDown(bot, targetY) {
    while (!_shouldAbort()) {
        const feet = bot.entity.position.floored()
        if (feet.y <= targetY) return

        for (const dy of [-1, -2, -3]) {
            const b = bot.blockAt(feet.offset(0, dy, 0))
            if (!b) continue
            if (_isLava(b) || _hasAdjacentLava(bot, b.position)) {
                console.log('[Mine] 直挖：下方偵測到岩漿，中止')
                return
            }
        }

        const below = bot.blockAt(feet.offset(0, -1, 0))
        if (below && below.boundingBox === 'block' && below.hardness >= 0) {
            try { await _equipToolForDig(bot, below); await _dig(bot, below) } catch (e) {
                console.log(`[Mine] 直挖失敗: ${e.message}`)
                return
            }
        }

        _setMovements(bot)
        try {
            await _goto(bot, new goals.GoalBlock(feet.x, feet.y - 1, feet.z), 3000)
        } catch (_) { return }
        activityStack.touch('mining', 'descending')
        await _sleep(150)
    }
}

// 挖 2×2 隧道往前，回傳是否有成功前進（挖到方塊 或 實際移動）
async function _digTunnel(bot, yaw, length = 8, targetY = null, goal = {}, expectedGen = -1) {
    let progressed = false
    let noProgressSteps = 0
    const dx = Math.round(-Math.sin(yaw))
    const dz = Math.round(-Math.cos(yaw))
    // 垂直於前進方向的側邊偏移
    const perpX = dz
    const perpZ = -dx

    for (let i = 0; i < length; i++) {
        if (!isMining || (expectedGen >= 0 && _loopGen !== expectedGen)) return false
        if (eating.isEating()) {
            await _sleep(250)
            continue
        }
        const base = bot.entity.position.floored()
        const baseY = targetY !== null ? targetY : base.y
        const feetPos  = base.offset(dx, baseY - base.y, dz)
        const headPos  = feetPos.offset(0, 1, 0)
        const feetPos2 = feetPos.offset(perpX, 0, perpZ)
        const headPos2 = feetPos2.offset(0, 1, 0)

        await _clearClimbablesAround(bot, [
            feetPos,
            headPos,
            feetPos.offset(0, 2, 0),
            feetPos2,
            headPos2,
            feetPos2.offset(0, 2, 0),
        ])

        for (const pos of [feetPos, headPos, feetPos2, headPos2]) {
            await _waitForHazardEscape(expectedGen)
            if (_shouldAbort(expectedGen)) return false
            if (_isNearWaterHazard(pos)) {
                console.log('[Tunnel] 前方靠近已知水域，放棄此方向')
                return false
            }
            if (isBuried(pos)) { console.log(`[Tunnel] 跳過 buried ${pos}`); continue }
            const b = bot.blockAt(pos)
            if (!b || b.name === 'air' || b.name === 'cave_air') continue
            if (_isWaterBlock(b) || _hasAdjacentWater(bot, pos)) {
                console.log(`[Tunnel] 偵測到水域 ${b.name} at ${pos}，標記並中止隧道`)
                hazards.remember('water', pos, 120000, 8)
                return false
            }
            if (_isLava(b)) {
                // 已經是岩漿（可見），嘗試封堵後中止
                console.log(`[Tunnel] 偵測到可見岩漿 at ${pos}，封堵並中止`)
                await _tryBlockLava(bot, pos)
                return false
            }
            if (b.hardness < 0) { console.log(`[Tunnel] 跳過基岩 ${pos}`); continue }
            if (_hasAdjacentLava(bot, b.position)) {
                // 隱藏岩漿：挖開後會流出，跳過此方塊
                console.log(`[Tunnel] ${b.name} 鄰近岩漿，跳過`)
                continue
            }
            try {
                // 只使用現有工具，不嵌入合成
                const needsPickaxe = b.name.includes('stone') || b.name.endsWith('_ore') ||
                    b.name.includes('deepslate') || b.name.includes('tuff') ||
                    b.name.includes('cobblestone') || b.name.includes('andesite') ||
                    b.name.includes('granite') || b.name.includes('diorite') ||
                    b.name.includes('basalt') || b.name.includes('netherrack')
                const pick = bot.inventory.items().find(i => i.name.endsWith('_pickaxe'))
                if (needsPickaxe && !pick) {
                    console.log(`[Tunnel] 無稿子無法挖 ${b.name}，中止隧道`)
                    return false
                }
                if (pick) {
                    try { await bot.equip(pick, 'hand') } catch (_) {}
                } else {
                    const tool = bot.pathfinder.bestHarvestTool(b)
                    if (tool) try { await bot.equip(tool, 'hand') } catch (_) {}
                }
                // 工具切換期間岩漿可能流入，重新確認
                const recheck = bot.blockAt(pos)
                if (!recheck || _isLava(recheck)) {
                    console.log(`[Tunnel] ${pos} 在工具切換後變為岩漿，中止`)
                    return false
                }
                await _dig(bot, b)
                progressed = true
                if (goal.target && b.name.includes(goal.target)) {
                    _targetCount++
                    activityStack.updateProgress({ count: _targetCount })
                    console.log(`[Mine] 挖下 ${b.name} (目標 ${_targetCount}/${goal.count})`)
                }
            } catch (e) { console.log(`[Tunnel] dig ${b.name} 失敗: ${e.message}`) }

            // 挖後立即確認該位置是否出現岩漿
            const afterDig = bot.blockAt(pos)
            if (_isLava(afterDig)) {
                console.log(`[Tunnel] 挖開後岩漿流入 ${pos}，嘗試封堵並中止`)
                await _tryBlockLava(bot, pos)
                return false
            }
        }

        // 目標比目前位置高時，啟用疊方塊讓 pathfinder 能墊上去
        if (feetPos.y > base.y) _setEscapeMovements(bot)
        else _setMovements(bot)
        if (_shouldAbort(expectedGen)) return false  // 被 stopMining 中止，不要清除其他 activity 的路徑
        bot.pathfinder.setGoal(null)  // 清除 setMovements 可能觸發的殘留 goal
        const prevPos = bot.entity.position.clone()
        try {
            await _goto(bot, new goals.GoalBlock(feetPos.x, feetPos.y, feetPos.z), 5000)
            if (bot.entity.position.distanceTo(prevPos) > 0.5) {
                progressed = true
                noProgressSteps = 0
            } else {
                if (++noProgressSteps >= 3) {
                    console.log('[Tunnel] 連續 3 步無法前進，放棄此方向')
                    break
                }
            }
        } catch (e) {
            if (e.message.includes('goal was changed') || e.message.includes('goal changed')) {
                // water/lava escape cancelled pathfinder — wait and retry once
                while (water.isEscaping() && !_shouldAbort()) await _sleep(300)
                await _sleep(200)
                try {
                    await _goto(bot, new goals.GoalBlock(feetPos.x, feetPos.y, feetPos.z), 5000)
                    if (bot.entity.position.distanceTo(prevPos) > 0.5) progressed = true
                } catch (e2) { console.log(`[Tunnel] GoalBlock 失敗: ${e2.message}`); break }
            } else {
                console.log(`[Tunnel] GoalBlock 失敗: ${e.message}`)
                break
            }
        }
        _setMovements(bot)

        await _sleep(200)

        // 每步完成後掃描附近新 exposed 的礦 — 有就讓主 loop 去挖
        const newlyExposed = bot.findBlocks({
            matching: b => b.name.endsWith('_ore') && ![...SKIP_ORES].some(s => b.name.includes(s)),
            maxDistance: 8,
            count: 10,
        }).filter(p => {
            if (!_isExposed(bot, p)) return false
            if (_digFailed.has(p.toString())) return false
            if (_hasAdjacentLava(bot, p)) return false
            if (_isNearWaterHazard(p)) return false
            if (goal.target) return bot.blockAt(p)?.name.includes(goal.target)
            return true
        })
        if (newlyExposed.length > 0) {
            console.log(`[Tunnel] 發現 ${newlyExposed.length} 個新礦石，暫停隧道`)
            return true
        }
    }
    return progressed
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
        if (_shouldAbort()) return
        if (eating.isEating()) {
            await _sleep(250)
            continue
        }
        try {
            await _goto(bot, new goals.GoalNear(e.position.x, e.position.y, e.position.z, 1), 5000)
            await _sleep(150)
        } catch (_) {}
    }
}

function _canMoveHorizontally(bot) {
    const feet = bot.entity.position.floored()
    return [[1,0,0],[-1,0,0],[0,0,1],[0,0,-1]].some(([dx,,dz]) => {
        const b1 = bot.blockAt(feet.offset(dx, 0, dz))
        const b2 = bot.blockAt(feet.offset(dx, 1, dz))
        return (!b1 || b1.boundingBox !== 'block') && (!b2 || b2.boundingBox !== 'block')
    })
}

async function _digEscape(bot, stopY = 60) {
    let lastY = Math.floor(bot.entity.position.y)
    let stuckTicks = 0

    for (let i = 0; i < 120 && Math.floor(bot.entity.position.y) < stopY; i++) {
        if (_shouldAbort()) return
        if (eating.isEating()) {
            await _sleep(250)
            continue
        }
        if (_canMoveHorizontally(bot)) {
            console.log(`[Mine] 逃脫到可移動區域 Y=${Math.floor(bot.entity.position.y)}`)
            return
        }
        const feet = bot.entity.position.floored()

        for (const dy of [2, 1]) {
            const b = bot.blockAt(feet.offset(0, dy, 0))
            if (!b || b.hardness < 0 || b.boundingBox !== 'block') continue
            try { await _equipToolForDig(bot, b); await _dig(bot, b) } catch (_) {}
        }

        _setEscapeMovements(bot)
        try {
            await _goto(bot, new goals.GoalBlock(feet.x, feet.y + 1, feet.z), 5000)
        } catch (_) {}

        await _sleep(150)

        const nowY = Math.floor(bot.entity.position.y)
        if (nowY <= lastY) {
            if (++stuckTicks >= 6) {
                console.log('[Mine] 被基岩困住，請求協助')
                bot.chat('我被困在基岩裡了，請用 /tp 把我傳出去！')
                return
            }
        } else { stuckTicks = 0; lastY = nowY }
    }
    console.log(`[Mine] 逃脫到 Y=${Math.floor(bot.entity.position.y)}`)
}

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

// pathfinder.goto 若超過 ms 毫秒沒有結果就拋出 timeout，並取消 pathfinder
function _goto(bot, goal, ms = 8000) {
    if (_shouldAbort()) {
        return Promise.reject(new Error('mining aborted'))
    }
    if (navigation.enabled()) {
        const gen = loopContext.getStore() ?? _loopGen
        return navigation.navigate(bot, goal, {
            timeoutMs: ms,
            shouldContinue: () => !_shouldAbort(gen) && !water.isEscaping() && activityStack.isTopActivity('mining'),
            onProgress: () => activityStack.touch('mining', 'navigation_waypoint'),
        }).then(result => {
            if (result.status !== 'success') {
                const e = new Error(`navigation ${result.status}: ${result.reason}`)
                e.navigation = result
                throw e
            }
        })
    }
    let done = false
    let timer = null
    const gotoPromise = bot.pathfinder.goto(goal).then(
        v => {
            done = true
            if (timer) clearTimeout(timer)
            if (_shouldAbort()) throw new Error('mining aborted')
            return v
        },
        e => {
            if (timer) clearTimeout(timer)
            if (!done) throw e
        }  // 若已 timeout 則靜默吞掉舊 goal 的 rejection
    )
    return Promise.race([
        gotoPromise,
        new Promise((_, reject) => {
            timer = setTimeout(() => {
                if (done) return
                done = true
                bot.pathfinder.setGoal(null)  // 正確取消 pathfinder，避免殘留 goal 干擾下一次 goto
                reject(new Error('pathfinder timeout'))
            }, ms)
        }),
    ])
}

function isActive() {
    return isMining
}

// shim: kept for inventory.js until step 6
function resumeMining() { isMining = true }

// shim: kept for combat.js until step 5
function getGoal() { return _currentGoal }

module.exports = { startMining, stopMining, isActive, resumeMining, getGoal }

// Each activity has its own cancellable async scope, including nested activities.
for (const [name, fn] of Object.entries(module.exports)) {
    if (name.startsWith('start') && name !== 'startMonitor') module.exports[name] = execution.owned(fn)
}
