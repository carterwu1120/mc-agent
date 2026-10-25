const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')

function loadMining() {
    const stubs = {
        './mining_phase': require('../mining_phase'), './activity': require('../activity'),
        './execution': require('../execution'), './crafting': {}, './bridge': { sendState() {} },
        './buried': {}, './eating': { isEating: () => false }, './water': {},
        './hazards': { isNear: () => false }, './movement_prefs': { applyMovements: () => ({}) },
        './navigation': { enabled: () => false, cancel() {}, lastResult: () => null },
    }
    const context = { module: { exports: {} }, require: name => stubs[name] || require(name),
        console: { log() {} }, process, setTimeout, clearTimeout, Date }
    vm.runInNewContext(fs.readFileSync(require.resolve('../mining'), 'utf8'), context)
    return context
}

test('a stair step is only taken onto solid ground', () => {
    // Seen live: the staircase broke into a cave at y=-1 and the bot fell to y=-18.
    const { _hasSolidFloor } = loadMining()
    const botWith = block => ({ blockAt: () => block })
    assert.equal(_hasSolidFloor(botWith({ name: 'stone', boundingBox: 'block' }), {}), true)
    assert.equal(_hasSolidFloor(botWith({ name: 'cave_air', boundingBox: 'empty' }), {}), false)
    assert.equal(_hasSolidFloor(botWith({ name: 'water', boundingBox: 'empty' }), {}), false)
    assert.equal(_hasSolidFloor(botWith({ name: 'lava', boundingBox: 'empty' }), {}), false)
    assert.equal(_hasSolidFloor(botWith(null), {}), false, 'unloaded counts as unsafe')
})
