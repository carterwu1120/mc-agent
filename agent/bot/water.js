const { goals } = require('mineflayer-pathfinder')
const { getActivity } = require('./activity')
const hazards = require('./hazards')
const { applyMovements } = require('./movement_prefs')

let _escaping = false
let _escapingLava = false
let _escapingSuffocation = false
let _lastCheck = 0
let _escapeCooldownUntil = 0
let _inWaterSince = 0   // timestamp when bot first entered water this stretch
let _lastWaterLoop = null
const CHECK_INTERVAL = 500
const WATER_DEBOUNCE = 1500  // ms in water before triggering escape
const WATER_LOOP_WINDOW = 30000
const WATER_LOOP_RADIUS = 8

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

function _isLiquid(name) {
    return name === 'water' || name === 'flowing_water' || name === 'lava' || name === 'flowing_lava'
}

function _isOnDryGround(bot) {
    if (!bot.entity.onGround) return false
    const below = bot.blockAt(bot.entity.position.offset(0, -0.1, 0))
    if (!below) return false
    return below.boundingBox === 'block' && !_isLiquid(below.name)
}

function _rememberNearbyWater(bot) {
    const pos = bot.entity.position.floored()
    for (let dx = -2; dx <= 2; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
            for (let dz = -2; dz <= 2; dz++) {
                const block = bot.blockAt(pos.offset(dx, dy, dz))
                if (_isLiquid(block?.name) && (block.name === 'water' || block.name === 'flowing_water')) {
                    hazards.remember('water', block.position, 180000, 10)
                }
            }
        }
    }
    hazards.remember('water', bot.entity.position, 180000, 10)
}

function _distSq(a, b) {
    if (!a || !b) return Infinity
    const dx = a.x - b.x
    const dy = a.y - b.y
    const dz = a.z - b.z
    return dx * dx + dy * dy + dz * dz
}

function _registerWaterEscape(bot) {
    const pos = bot.entity.position.floored()
    const now = Date.now()
    if (
        _lastWaterLoop &&
        (now - _lastWaterLoop.lastAt) <= WATER_LOOP_WINDOW &&
        _distSq(_lastWaterLoop.pos, pos) <= WATER_LOOP_RADIUS * WATER_LOOP_RADIUS
    ) {
        _lastWaterLoop = {
            pos,
            lastAt: now,
            count: _lastWaterLoop.count + 1,
        }
    } else {
        _lastWaterLoop = { pos, lastAt: now, count: 1 }
    }
    return _lastWaterLoop.count
}

// 掃描周圍找最近的安全站立位置（腳 + 頭都是空氣，地板是實心且非岩漿）
function _findDryBlock(bot, radius = 8, options = {}) {
    const pos = bot.entity.position
    let best = null
    let bestScore = Infinity
    const preferFarFrom = options.preferFarFrom ?? null
    const minFromCenter = options.minFromCenter ?? 0
    const avoidWaterRadius = options.avoidWaterRadius ?? 0

    for (let dy = -2; dy <= radius; dy++) {
        for (let dx = -radius; dx <= radius; dx++) {
            for (let dz = -radius; dz <= radius; dz++) {
                const feet = pos.offset(dx, dy, dz).floored()
                const feetB  = bot.blockAt(feet)
                const headB  = bot.blockAt(feet.offset(0, 1, 0))
                const floorB = bot.blockAt(feet.offset(0, -1, 0))
                if (!feetB || !headB || !floorB) continue
                const isAir = n => n === 'air' || n === 'cave_air'
                if (!isAir(feetB.name) || !isAir(headB.name)) continue
                if (floorB.boundingBox !== 'block') continue  // 要有地板
                if (_isLiquid(floorB.name)) continue          // 地板不能是岩漿/水
                if (avoidWaterRadius > 0 && hazards.isNear(feet, 'water', avoidWaterRadius)) continue

                const dist = Math.abs(dx) + Math.abs(dy) * 0.5 + Math.abs(dz)
                if (preferFarFrom) {
                    const fromCenter = Math.sqrt(_distSq(feet, preferFarFrom))
                    if (fromCenter < minFromCenter) continue
                    const score = dist - fromCenter * 2
                    if (score < bestScore) { bestScore = score; best = feet }
                } else if (dist < bestScore) {
                    bestScore = dist
                    best = feet
                }
            }
        }
    }
    return best
}

async function _retreatFromWater(bot) {
    const origin = bot.entity.position.floored()
    const retreat =
        _findDryBlock(bot, 18, {
            preferFarFrom: origin,
            minFromCenter: 6,
            avoidWaterRadius: 8,
        }) ||
        _findDryBlock(bot, 18, {
            preferFarFrom: origin,
            minFromCenter: 4,
            avoidWaterRadius: 5,
        })

    if (!retreat) {
        console.log('[Water] 找不到足夠遠的乾燥落腳點，維持原地')
        return false
    }

    console.log(`[Water] 連續遇水，後撤到較乾燥位置 (${retreat.x}, ${retreat.y}, ${retreat.z})`)
    try {
        applyMovements(bot, { canDig: false })
        await Promise.race([
            bot.pathfinder.goto(new goals.GoalNear(retreat.x, retreat.y, retreat.z, 1)),
            _sleep(10000).then(() => bot.pathfinder.setGoal(null)),
        ])
    } catch (_) {
    } finally {
        applyMovements(bot)
    }

    const movedFarEnough = _distSq(origin, bot.entity.position.floored()) >= 16
    return !bot.entity.isInWater && movedFarEnough
}

// 掃描周圍找可以站立的岸邊，距離近的優先，走過去並跳上
async function _tryClimbShore(bot) {
    const pos = bot.entity.position.floored()
    const RADIUS = 5
    const candidates = []

    for (let dx = -RADIUS; dx <= RADIUS; dx++) {
        for (let dz = -RADIUS; dz <= RADIUS; dz++) {
            if (dx === 0 && dz === 0) continue
            for (const dy of [1, 0, -1]) {
                const b     = bot.blockAt(pos.offset(dx, dy, dz))
                const above = bot.blockAt(pos.offset(dx, dy + 1, dz))
                const above2 = bot.blockAt(pos.offset(dx, dy + 2, dz))
                if (!b || !above) continue
                if (b.boundingBox !== 'block') continue
                if (_isLiquid(b.name)) continue
                if (above.boundingBox === 'block' || above2?.boundingBox === 'block') continue
                const dist = Math.sqrt(dx * dx + dz * dz) + Math.abs(dy) * 0.5
                candidates.push({ x: pos.x + dx, y: pos.y + dy + 1, z: pos.z + dz, dist })
                break
            }
        }
    }

    if (candidates.length === 0) return false
    candidates.sort((a, b) => a.dist - b.dist)

    const target = candidates[0]
    console.log(`[Water] 找到岸邊 (${target.x}, ${target.y}, ${target.z})，嘗試爬上去`)

    // 先 pathfind 走近，再手動跳
    applyMovements(bot, { canDig: false, allowWater: true })
    try {
        await Promise.race([
            bot.pathfinder.goto(new goals.GoalNear(target.x, target.y, target.z, 1)),
            _sleep(5000).then(() => bot.pathfinder.setGoal(null)),
        ])
    } catch (_) {}
    applyMovements(bot)

    if (!bot.entity.isInWater) return true

    // pathfind 沒完全到位，手動 face + jump + forward
    try { await bot.lookAt({ x: target.x, y: target.y, z: target.z }) } catch (_) {}
    bot.setControlState('jump', true)
    bot.setControlState('forward', true)
    await _sleep(1500)
    bot.setControlState('forward', false)
    bot.setControlState('jump', false)

    return !bot.entity.isInWater
}

async function _tryEscape(bot) {
    if (_escaping) return
    _escaping = true
    console.log('[Water] 偵測到在水中，嘗試逃脫...')

    bot.pathfinder?.setGoal(null)

    // ── 第零階段：掃描相鄰岸邊，直接跳上去 ───────────────
    console.log('[Water] 掃描附近岸邊...')
    if (await _tryClimbShore(bot)) {
        console.log('[Water] 跳上岸成功')
        _rememberNearbyWater(bot)
        const loopCount = _registerWaterEscape(bot)
        if (loopCount >= 2) {
            console.log(`[Water] 同區域連續遇水 ${loopCount} 次，嘗試先遠離水域`)
            await _retreatFromWater(bot)
        }
        _escapeCooldownUntil = Date.now() + 3000
        _escaping = false
        return
    }

    // ── 第一階段：往上游 4 秒 ────────────────────────────
    const phase1 = Date.now() + 4000
    while (bot.entity.isInWater && Date.now() < phase1) {
        bot.setControlState('jump', true)
        await _sleep(300)
    }
    bot.setControlState('jump', false)

    if (!bot.entity.isInWater) {
        // 確認真的踩在陸地上，而不是浮在水面
        await _sleep(800)
        if (_isOnDryGround(bot)) {
            console.log('[Water] 游上來成功')
            _rememberNearbyWater(bot)
            const loopCount = _registerWaterEscape(bot)
            if (loopCount >= 2) {
                console.log(`[Water] 同區域連續遇水 ${loopCount} 次，嘗試先遠離水域`)
                await _retreatFromWater(bot)
            }
            _escapeCooldownUntil = Date.now() + 3000
            _escaping = false
            return
        }
        console.log('[Water] 仍未踩穩，繼續逃脫...')
    }

    // ── 第二階段：掃描周圍乾燥方塊，pathfind 過去 ────────
    console.log('[Water] 往上游無效（水從上方流下），掃描周圍出口...')
    const dry = _findDryBlock(bot, 16)
    if (dry) {
        console.log(`[Water] 找到出口 ${dry}，嘗試導航`)
        applyMovements(bot, { canDig: false, allowWater: true })
        try {
            await Promise.race([
                bot.pathfinder.goto(new goals.GoalNear(dry.x, dry.y, dry.z, 1)),
                _sleep(8000).then(() => { bot.pathfinder.setGoal(null) }),
            ])
        } catch (_) {}
        // 重設 movements
        applyMovements(bot)
    }

    if (!bot.entity.isInWater && _isOnDryGround(bot)) {
        console.log('[Water] 導航出水成功')
        _rememberNearbyWater(bot)
        const loopCount = _registerWaterEscape(bot)
        if (loopCount >= 2) {
            console.log(`[Water] 同區域連續遇水 ${loopCount} 次，嘗試先遠離水域`)
            await _retreatFromWater(bot)
        }
        _escapeCooldownUntil = Date.now() + 3000
        _escaping = false
        return
    }

    // ── 第三階段：嘗試四個水平方向強行移動 ──────────────
    console.log('[Water] 導航失敗，嘗試水平逃脫...')
    const dirs = ['forward', 'back', 'left', 'right']
    for (const dir of dirs) {
        if (!bot.entity.isInWater) break
        bot.setControlState('jump', true)
        bot.setControlState(dir, true)
        await _sleep(2000)
        bot.setControlState(dir, false)
        bot.setControlState('jump', false)
        await _sleep(300)
    }

    if (bot.entity.isInWater) {
        console.log('[Water] 無法逃脫水中，請求協助')
        bot.chat('我被困在水裡了，請救我！')
    } else {
        console.log('[Water] 水平移動逃脫成功')
        _rememberNearbyWater(bot)
        const loopCount = _registerWaterEscape(bot)
        if (loopCount >= 2) {
            console.log(`[Water] 同區域連續遇水 ${loopCount} 次，嘗試先遠離水域`)
            await _retreatFromWater(bot)
        }
    }

    _escaping = false
}

async function _tryEscapeLava(bot) {
    if (_escapingLava) return
    _escapingLava = true
    console.log('[Hazard] 偵測到在岩漿中，緊急逃脫！')

    bot.pathfinder?.setGoal(null)

    // ── 第一階段：跳出岩漿（比水更緊急，只等 2 秒）────────
    const phase1 = Date.now() + 2000
    while (bot.entity.isInLava && Date.now() < phase1) {
        bot.setControlState('jump', true)
        await _sleep(200)
    }
    bot.setControlState('jump', false)

    if (!bot.entity.isInLava) {
        console.log('[Hazard] 跳出岩漿成功')
        _escapingLava = false
        return
    }

    // ── 第二階段：掃描安全位置，pathfind 過去 ────────────
    console.log('[Hazard] 尋找安全出口...')
    const safe = _findDryBlock(bot, 8)
    if (safe) {
        applyMovements(bot, { canDig: false, allowWater: true })
        try {
            await Promise.race([
                bot.pathfinder.goto(new goals.GoalNear(safe.x, safe.y, safe.z, 1)),
                _sleep(6000).then(() => { bot.pathfinder.setGoal(null) }),
            ])
        } catch (_) {}
        applyMovements(bot)
    }

    if (!bot.entity.isInLava) {
        console.log('[Hazard] 導航出岩漿成功')
        _escapingLava = false
        return
    }

    // ── 第三階段：四方向強行移動 ─────────────────────────
    console.log('[Hazard] 強行水平逃脫...')
    const dirs = ['forward', 'back', 'left', 'right']
    for (const dir of dirs) {
        if (!bot.entity.isInLava) break
        bot.setControlState('jump', true)
        bot.setControlState(dir, true)
        await _sleep(1500)
        bot.setControlState(dir, false)
        bot.setControlState('jump', false)
        await _sleep(200)
    }

    if (bot.entity.isInLava) {
        console.log('[Hazard] 無法逃脫岩漿，請求協助')
        bot.chat('我被困在岩漿裡了，請救我！')
    } else {
        console.log('[Hazard] 逃脫岩漿成功')
    }

    _escapingLava = false
}

function _isSolid(block) {
    if (!block) return false
    if (block.boundingBox !== 'block') return false
    if (_isLiquid(block.name)) return false
    return true
}

function _isSuffocating(bot) {
    const feet = bot.entity.position.floored()
    const head = feet.offset(0, 1, 0)
    const feetBlock = bot.blockAt(feet)
    const headBlock = bot.blockAt(head)
    return _isSolid(feetBlock) || _isSolid(headBlock)
}

async function _tryEscapeSuffocation(bot) {
    if (_escapingSuffocation) return
    _escapingSuffocation = true
    console.log('[Hazard] 偵測到被困在實心方塊中，嘗試挖出！')

    bot.pathfinder?.setGoal(null)

    const pos = bot.entity.position.floored()
    // Priority: dig feet block first, then head, then above head, then surrounding
    const targets = [
        pos,
        pos.offset(0, 1, 0),
        pos.offset(0, 2, 0),
        pos.offset(0, -1, 0),
        pos.offset(1, 0, 0), pos.offset(-1, 0, 0),
        pos.offset(0, 0, 1), pos.offset(0, 0, -1),
    ]

    for (const target of targets) {
        const block = bot.blockAt(target)
        if (!block || !_isSolid(block)) continue
        if (block.name === 'bedrock') continue
        try {
            console.log(`[Hazard] 挖掉 ${block.name} at (${target.x}, ${target.y}, ${target.z})`)
            const tool = bot.pathfinder.bestHarvestTool(block)
            if (tool) await bot.equip(tool, 'hand')
            await bot.dig(block, true)
            await _sleep(100)
        } catch (e) {
            console.log(`[Hazard] 挖掘失敗: ${e.message}`)
        }
        if (!_isSuffocating(bot)) break
    }

    if (_isSuffocating(bot)) {
        console.log('[Hazard] 無法挖出，嘗試跳躍逃脫')
        bot.setControlState('jump', true)
        await _sleep(1000)
        bot.setControlState('jump', false)
    }

    _escapingSuffocation = false
}

function startMonitor(bot) {
    bot.on('physicsTick', () => {
        if (getActivity() === 'fishing') return

        const now = Date.now()
        if (now - _lastCheck < CHECK_INTERVAL) return
        _lastCheck = now

        // 窒息優先（被傳送進牆裡）
        if (!_escapingSuffocation && !_escapingLava && !_escaping && _isSuffocating(bot)) {
            _tryEscapeSuffocation(bot).catch(e => console.log('[Hazard] 窒息逃脫失敗:', e.message))
            return
        }

        // 岩漿優先（更危險）
        if (!_escapingLava && !_escaping && !_escapingSuffocation && bot.entity.isInLava) {
            _tryEscapeLava(bot).catch(e => console.log('[Hazard] 岩漿逃脫失敗:', e.message))
            return
        }

        if (!_escaping && !_escapingLava && !_escapingSuffocation && Date.now() > _escapeCooldownUntil) {
            if (bot.entity.isInWater) {
                if (_inWaterSince === 0) _inWaterSince = now
                if (now - _inWaterSince >= WATER_DEBOUNCE) {
                    _inWaterSince = 0
                    _tryEscape(bot).catch(e => console.log('[Water] 逃脫失敗:', e.message))
                }
            } else {
                _inWaterSince = 0
            }
        }
    })

    console.log('[Hazard] 水/岩漿危機監控已啟動')
}

function isEscaping() {
    return _escaping || _escapingLava || _escapingSuffocation
}

module.exports = { startMonitor, isEscaping }
