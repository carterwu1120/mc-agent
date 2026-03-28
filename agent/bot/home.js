const fs = require('fs')
const path = require('path')
const activityStack = require('./activity')

const DATA_FILE = path.join(__dirname, '..', 'data', 'home.json')

function _load() {
    try {
        return JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'))
    } catch (_) {
        return null
    }
}

function _save(data) {
    fs.mkdirSync(path.dirname(DATA_FILE), { recursive: true })
    fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2))
}

function setHome(bot) {
    const pos = bot.entity.position
    const home = { x: Math.floor(pos.x), y: Math.floor(pos.y), z: Math.floor(pos.z) }
    _save(home)
    console.log(`[Home] 基地已設定在 (${home.x}, ${home.y}, ${home.z})`)
    bot.chat(`基地已設定在 (${home.x}, ${home.y}, ${home.z})`)
}

let _returnPos = null  // position recorded when goHome is called

function goHome(bot) {
    const home = _load()
    if (!home) {
        console.log('[Home] 尚未設定基地，請先用 !sethome')
        bot.chat('尚未設定基地，請先用 !sethome')
        return false
    }
    const pos = bot.entity.position
    _returnPos = { x: Math.floor(pos.x), y: Math.floor(pos.y), z: Math.floor(pos.z) }
    console.log(`[Home] 傳送回基地 (${home.x}, ${home.y}, ${home.z})，記住當前位置 (${_returnPos.x}, ${_returnPos.y}, ${_returnPos.z})`)
    bot.chat(`/tp ${bot.username} ${home.x} ${home.y} ${home.z}`)
    return true
}

function getHome() {
    return _load()
}

function back(bot) {
    const stack = activityStack.getStack()
    const top = stack[stack.length - 1]
    if (!top) {
        console.log('[Back] 沒有活動可返回')
        bot.chat('沒有活動可返回')
        return false
    }
    // 優先用 goHome 前記住的位置，否則 fallback 到 startPos
    const sp = _returnPos ?? top.startPos
    _returnPos = null
    if (!sp) {
        console.log('[Back] 沒有記錄的返回點')
        bot.chat('沒有記錄的返回點')
        return false
    }
    const pos = bot.entity.position
    const dist = Math.sqrt((pos.x - sp.x) ** 2 + (pos.y - sp.y) ** 2 + (pos.z - sp.z) ** 2)
    console.log(`[Back] 返回 (${sp.x}, ${sp.y}, ${sp.z})，距離 ${dist.toFixed(0)} 格`)
    if (dist > 10) {
        bot.chat(`/tp ${bot.username} ${sp.x} ${sp.y} ${sp.z}`)
    }
    setTimeout(() => activityStack.resumeCurrent(bot), 1000)
    return true
}

module.exports = { setHome, goHome, getHome, back }
