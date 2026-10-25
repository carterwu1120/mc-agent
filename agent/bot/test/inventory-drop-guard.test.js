const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')

function load() {
    const module = { exports: {} }
    const stubs = { './bridge': { sendState() {} }, './activity': require('../activity'), './execution': require('../execution') }
    vm.runInNewContext(fs.readFileSync(require.resolve('../inventory'), 'utf8'), {
        module, exports: module.exports, require: name => stubs[name] || require(name),
        console: { log() {} }, setTimeout, clearTimeout, setInterval, clearInterval,
    })
    return module.exports
}

function makeBot(items) {
    const tossed = []
    return {
        tossed,
        entity: { yaw: 0 },
        look: async () => {},
        inventory: { items: () => items },
        toss: async (type, _meta, count) => {
            tossed.push([items.find(i => i.type === type).name, count])
        },
    }
}

const item = (name, count, type) => ({ name, count, type })

test('guarded items are never tossed, whoever asked for them', async () => {
    const inv = load()
    const bot = makeBot([item('diamond', 2, 1), item('iron_pickaxe', 1, 2), item('cooked_beef', 5, 3),
        item('raw_iron', 3, 4), item('lapis_block', 2, 5), item('feather', 9, 6)])
    await inv.buryItems(bot, new Map([['diamond', 2], ['iron_pickaxe', 1], ['cooked_beef', 5],
        ['raw_iron', 3], ['lapis_block', 2], ['feather', 9]]))
    assert.deepEqual(bot.tossed, [['feather', 9]])
})

test('at least 128 building blocks stay in total for pillaring', async () => {
    const inv = load()
    const bot = makeBot([item('cobblestone', 100, 1), item('dirt', 60, 2)])
    await inv.buryItems(bot, new Map([['cobblestone', 100], ['dirt', 60]]))
    assert.equal(bot.tossed.reduce((n, [, c]) => n + c, 0), 32)
})
