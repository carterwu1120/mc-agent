const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const activity = require('../activity')

function loadHome() {
    const module = { exports: {} }
    const stubs = { './activity': activity, './crafting': { noteTeleportLikeAction() {} }, './movement_prefs': { applyMovements() {} } }
    vm.runInNewContext(fs.readFileSync(require.resolve('../home'), 'utf8'), {
        module, exports: module.exports, require: name => stubs[name] || require(name),
        console: { log() {} }, process, setTimeout: () => {}, __dirname: require('node:path').dirname(require.resolve('../home')),
    })
    return module.exports
}

function makeBot(x, y, z) {
    const chats = []
    return { chats, entity: { position: { x, y, z } }, chat: text => chats.push(text),
        pathfinder: { setGoal() {} }, clearControlStates() {}, stopDigging() {} }
}

test('back still returns to the task start after recovery suspend cleared the stack', () => {
    activity.clearStack()
    const home = loadHome()
    const atWork = makeBot(100, -16, -200)
    activity.push(atWork, 'mining', { target: 'iron', count: 3 }, () => {})
    activity.clearStack() // what suspend / stopall do before a recovery plan runs

    const wandered = makeBot(140, -35, -160)
    assert.equal(home.back(wandered), true)
    assert.deepEqual(wandered.chats, ['/tp undefined 100 -16 -200'])
})

test('self-defense combat does not replace the remembered task start', () => {
    activity.clearStack()
    const home = loadHome()
    activity.push(makeBot(1, 64, 1), 'mining', {}, () => {})
    activity.clearStack()
    activity.push(makeBot(50, 64, 50), 'combat', { defensive: true }, () => {})
    activity.clearStack()

    const bot = makeBot(60, 64, 60)
    home.back(bot)
    assert.deepEqual(bot.chats, ['/tp undefined 1 64 1'])
    activity.clearStack()
})
