const { getActivity } = require('./activity')

let _escaping = false
let _lastCheck = 0
const CHECK_INTERVAL = 500  // 每 500ms 檢查一次

function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms))
}

async function _tryEscape(bot) {
    if (_escaping) return
    _escaping = true
    console.log('[Water] 偵測到在水中，開始游出...')

    // 停止 pathfinder，改用跳躍游上來
    bot.pathfinder?.setGoal(null)

    const deadline = Date.now() + 15000
    while (bot.entity.isInWater && Date.now() < deadline) {
        bot.setControlState('jump', true)
        await _sleep(400)
    }
    bot.setControlState('jump', false)

    if (bot.entity.isInWater) {
        console.log('[Water] 無法游出水中')
    } else {
        console.log('[Water] 已游出水中')
    }

    _escaping = false
}

function startMonitor(bot) {
    bot.on('physicsTick', () => {
        // 釣魚時 bobber 在水裡是正常的，不觸發
        if (getActivity() === 'fishing') return
        if (_escaping) return

        const now = Date.now()
        if (now - _lastCheck < CHECK_INTERVAL) return
        _lastCheck = now

        if (bot.entity.isInWater) {
            _tryEscape(bot).catch(e => console.log('[Water] 逃脫失敗:', e.message))
        }
    })

    console.log('[Water] 水中逃脫監控已啟動')
}

function isEscaping() {
    return _escaping
}

module.exports = { startMonitor, isEscaping }
