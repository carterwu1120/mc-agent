const fs = require('fs')
const path = require('path')

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

function goHome(bot) {
    const home = _load()
    if (!home) {
        console.log('[Home] 尚未設定基地，請先用 !sethome')
        bot.chat('尚未設定基地，請先用 !sethome')
        return false
    }
    console.log(`[Home] 傳送回基地 (${home.x}, ${home.y}, ${home.z})`)
    bot.chat(`/tp ${bot.username} ${home.x} ${home.y} ${home.z}`)
    return true
}

function getHome() {
    return _load()
}

module.exports = { setHome, goHome, getHome }
