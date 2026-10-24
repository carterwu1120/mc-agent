const test = require('node:test')
const assert = require('node:assert/strict')
const phase = require('../mining_phase')
const fs = require('node:fs')
const vm = require('node:vm')

test('diamond strategy does not chase coal, including wide searches', () => {
    assert.equal(phase.matches('coal_ore', 'diamond'), false)
    assert.equal(phase.matches('deepslate_diamond_ore', 'diamond'), true)
    assert.equal(phase.matches('iron_ore', 'raw_iron'), true)
})

test('a blocked segment reports inventory progress separately from movement', () => {
    let items = [{ name: 'diamond', count: 2 }]
    const bot = { inventory: { items: () => items }, entity: { position: { x: 0, y: 42, z: 0 } } }
    const before = phase.snapshot(bot, 'diamond')
    bot.entity.position.y = 35
    items = [{ name: 'diamond', count: 2 }, { name: 'coal', count: 64 }]
    const result = phase.result(bot, { target: 'diamond', count: 10 }, before, 'descending', 'no_safe_route')
    assert.equal(result.status, 'blocked')
    assert.equal(result.changes.target_gained, 0)
    assert.equal(result.changes.position.before.y, 42)
    assert.equal(result.changes.position.after.y, 35)
})

test('missing iron-tier tool yields a decision before navigating or crafting', async () => {
    const events = []
    const activity = { register() {}, isStale: () => false, push() {}, markStarted() {},
        isTopActivity: () => true, pop() {} }
    const bot = { entity: { position: { x: 0, y: 42, z: 0 }, yaw: 0 },
        inventory: { items: () => [{ name: 'stone_pickaxe', count: 1 }] } }
    const module = { exports: {} }
    const stubs = { './mining_phase': phase, './activity': activity,
        './execution': require('../execution'),
        './bridge': { sendState: (_bot, type, data) => events.push({ type, ...data }) },
        './crafting': { ensurePickaxe: () => assert.fail('must not craft implicitly') } }
    vm.runInNewContext(fs.readFileSync(require.resolve('../mining'), 'utf8'), {
        module, exports: module.exports, process, setTimeout, clearTimeout,
        console: { log() {} },
        require: name => stubs[name] || (name.startsWith('./') ? {} : require(name)),
    })
    await module.exports.startMining(bot, { target: 'diamond', count: 10 })
    assert.equal(events.length, 1)
    assert.equal(events[0].type, 'mining_decision')
    assert.equal(events[0].mining_result.required_tool, 'iron_pickaxe')
    assert.equal(events[0].mining_result.changes.target_gained, 0)
})
