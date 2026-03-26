const { goals, Movements } = require('mineflayer-pathfinder')
const { getActivity } = require('./activity')

let _escaping = false
let _escapingLava = false
let _lastCheck = 0
const CHECK_INTERVAL = 500

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

function _isLiquid(name) {
    return name === 'water' || name === 'flowing_water' || name === 'lava' || name === 'flowing_lava'
}

// 掃描周圍找最近的安全站立位置（腳 + 頭都是空氣，地板是實心且非岩漿）
function _findDryBlock(bot, radius = 8) {
    const pos = bot.entity.position
    let best = null
    let bestDist = Infinity

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

                const dist = Math.abs(dx) + Math.abs(dy) * 0.5 + Math.abs(dz)
                if (dist < bestDist) { bestDist = dist; best = feet }
            }
        }
    }
    return best
}

async function _tryEscape(bot) {
    if (_escaping) return
    _escaping = true
    console.log('[Water] 偵測到在水中，嘗試逃脫...')

    bot.pathfinder?.setGoal(null)

    // ── 第一階段：往上游 4 秒 ────────────────────────────
    const phase1 = Date.now() + 4000
    while (bot.entity.isInWater && Date.now() < phase1) {
        bot.setControlState('jump', true)
        await _sleep(300)
    }
    bot.setControlState('jump', false)

    if (!bot.entity.isInWater) {
        console.log('[Water] 游上來成功')
        _escaping = false
        return
    }

    // ── 第二階段：掃描周圍乾燥方塊，pathfind 過去 ────────
    console.log('[Water] 往上游無效（水從上方流下），掃描周圍出口...')
    const dry = _findDryBlock(bot, 8)
    if (dry) {
        console.log(`[Water] 找到出口 ${dry}，嘗試導航`)
        const movements = new Movements(bot)
        movements.canDig = false
        bot.pathfinder.setMovements(movements)
        try {
            await Promise.race([
                bot.pathfinder.goto(new goals.GoalNear(dry.x, dry.y, dry.z, 1)),
                _sleep(8000).then(() => { bot.pathfinder.setGoal(null) }),
            ])
        } catch (_) {}
        // 重設 movements
        bot.pathfinder.setMovements(new Movements(bot))
    }

    if (!bot.entity.isInWater) {
        console.log('[Water] 導航出水成功')
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
        const movements = new Movements(bot)
        movements.canDig = false
        bot.pathfinder.setMovements(movements)
        try {
            await Promise.race([
                bot.pathfinder.goto(new goals.GoalNear(safe.x, safe.y, safe.z, 1)),
                _sleep(6000).then(() => { bot.pathfinder.setGoal(null) }),
            ])
        } catch (_) {}
        bot.pathfinder.setMovements(new Movements(bot))
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

function startMonitor(bot) {
    bot.on('physicsTick', () => {
        if (getActivity() === 'fishing') return

        const now = Date.now()
        if (now - _lastCheck < CHECK_INTERVAL) return
        _lastCheck = now

        // 岩漿優先（更危險）
        if (!_escapingLava && !_escaping && bot.entity.isInLava) {
            _tryEscapeLava(bot).catch(e => console.log('[Hazard] 岩漿逃脫失敗:', e.message))
            return
        }

        if (!_escaping && !_escapingLava && bot.entity.isInWater) {
            _tryEscape(bot).catch(e => console.log('[Water] 逃脫失敗:', e.message))
        }
    })

    console.log('[Hazard] 水/岩漿危機監控已啟動')
}

function isEscaping() {
    return _escaping || _escapingLava
}

module.exports = { startMonitor, isEscaping }
