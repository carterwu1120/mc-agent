const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const activity = require('../activity')

activity.register('smelting', () => {})

function load(events, smeltCalls) {
    const module = { exports: {} }
    const stubs = {
        './activity': activity,
        './execution': require('../execution'),
        './bridge': { sendState: (_bot, type, extra) => events.push({ type, ...extra }) },
        './eating': { RAW_FOOD_ITEMS: new Set(['chicken', 'beef']) },
        // Smelting pushes its own frame above getfood, like the real module.
        './smelting': { startSmelting: (bot, goal) => { smeltCalls.push(goal); activity.push(bot, 'smelting', goal, () => {}) } },
    }
    vm.runInNewContext(fs.readFileSync(require.resolve('../food_collect'), 'utf8'), {
        module, exports: module.exports, require: name => stubs[name] || require(name), console: { log() {} },
    })
    return module.exports
}

const tick = () => new Promise(r => setImmediate(r))

test('getfood cooks carried raw food and finishes when it is cooked', async () => {
    activity.clearStack()
    const events = [], smeltCalls = []
    const food = load(events, smeltCalls)
    const items = [{ name: 'chicken', count: 4 }]
    const bot = { entity: { position: { x: 0, y: 64, z: 0 } }, inventory: { items: () => items } }

    await food.startGetFood(bot, { count: 4 })
    await tick()
    assert.equal(JSON.stringify(smeltCalls), JSON.stringify([{ target: 'chicken', count: 4 }]))
    assert.equal(activity.getActivity(), 'smelting')
    assert.deepEqual(events.filter(e => e.type === 'activity_stuck'), [], 'no has_raw_food hand-off to the planner')

    items.splice(0, items.length, { name: 'cooked_chicken', count: 4 })
    activity.pop(bot) // smelting finished; getfood resumes
    await tick()
    assert.ok(events.some(e => e.type === 'activity_done' && e.activity === 'getfood'))
    activity.clearStack()
})

test('a cooking round that produces nothing stops instead of looping', async () => {
    activity.clearStack()
    const events = [], smeltCalls = []
    const food = load(events, smeltCalls)
    const items = [{ name: 'beef', count: 3 }]
    const bot = { entity: { position: { x: 0, y: 64, z: 0 } }, inventory: { items: () => items } }

    await food.startGetFood(bot, { count: 3 })
    await tick()
    activity.pop(bot) // smelting ended without output (e.g. no fuel)
    await tick()
    assert.equal(smeltCalls.length, 1)
    const stuck = events.filter(e => e.type === 'activity_stuck')
    assert.equal(stuck.length, 1)
    assert.equal(stuck[0].reason, 'cook_failed')
    activity.clearStack()
})
