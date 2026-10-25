const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { Vec3 } = require('vec3')
const activity = require('../activity')

// Timers fire on the next turn, so the 5 s chase timeout and the post-fight armor
// check run instantly instead of making the test wait.
const fastSetTimeout = (fn, _ms, ...args) => setImmediate(fn, ...args)

function loadCombat(events) {
    const module = { exports: {} }
    const stubs = {
        './activity': activity,
        './execution': require('../execution'),
        './bridge': { sendState: (_bot, type, extra) => events.push({ type, ...extra }) },
        './crafting': { ensureCraftingTable: async () => null, reclaimCraftingTable: async () => {}, ensureSword: async () => false },
        './smelting': { startSmelting() {} },
    }
    vm.runInNewContext(fs.readFileSync(require.resolve('../combat'), 'utf8'), {
        module, exports: module.exports, require: name => stubs[name] || require(name),
        console: { log() {} }, setTimeout: fastSetTimeout, clearTimeout, Date,
    })
    return module.exports
}

// A mob 12 blocks away that the pathfinder can never reach.
function makeBot({ mob = 'zombie', items = [{ name: 'wooden_sword', count: 1 }], health = 20 } = {}) {
    const gotos = []
    return {
        gotos,
        health,
        entity: { id: 1, position: new Vec3(0, 64, 0) },
        entities: { 2: { id: 2, name: mob, isValid: true, position: new Vec3(12, 64, 0), height: 1.9 } },
        heldItem: items[0] ?? null,
        inventory: { items: () => items, slots: new Array(46).fill(null) },
        pathfinder: { goto: goal => { gotos.push(goal.constructor.name); return Promise.reject(new Error('no path')) }, setGoal() {} },
        blockAt: () => ({ name: 'air' }),
        getEquipmentDestSlot: () => 5,
        equip: async () => {}, unequip: async () => {}, lookAt: async () => {}, attack() {},
    }
}

async function runUntilIdle(combat) {
    for (let i = 0; i < 200 && combat.isActive(); i++) await new Promise(r => setImmediate(r))
    for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)) // post-fight timers
}

test('self-defense does not chase beyond its leash or report the task as stuck', async () => {
    activity.clearStack()
    const events = []
    const combat = loadCombat(events)
    await combat.startCombat(makeBot(), { defensive: true })
    await runUntilIdle(combat)

    assert.equal(combat.isActive(), false)
    assert.equal(activity.getActivity(), 'idle', 'combat popped, so the interrupted task resumes')
    assert.deepEqual(events.filter(e => e.type === 'activity_stuck'), [])
    assert.deepEqual(events.filter(e => e.type === 'craft_decision'), [],
        'no armor auto-crafting after self-defense')
    activity.clearStack()
})

test('an explicit combat command still reports an unreachable target', async () => {
    activity.clearStack()
    const events = []
    const combat = loadCombat(events)
    await combat.startCombat(makeBot(), {})
    await runUntilIdle(combat)

    const stuck = events.filter(e => e.type === 'activity_stuck')
    assert.equal(stuck.length, 1)
    assert.equal(stuck[0].reason, 'cannot_reach_target')
    activity.clearStack()
})


test('an archer outside the leash is chased when the bot can fight back', async () => {
    // Seen live: a skeleton shot the bot 7 times while each defense ended "out of range".
    activity.clearStack()
    const events = []
    const combat = loadCombat(events)
    const bot = makeBot({ mob: 'skeleton' })
    await combat.startCombat(bot, { defensive: true })
    await runUntilIdle(combat)
    assert.ok(bot.gotos.includes('GoalNear'), 'closes in on the archer')
    assert.ok(!bot.gotos.includes('GoalInvert'))
    assert.deepEqual(events.filter(e => e.type === 'activity_stuck'), [])
    activity.clearStack()
})

test('an archer the bot cannot fight is escaped instead of ignored', async () => {
    activity.clearStack()
    const events = []
    const combat = loadCombat(events)
    for (const bot of [makeBot({ mob: 'skeleton', items: [] }), makeBot({ mob: 'skeleton', health: 4 })]) {
        await combat.startCombat(bot, { defensive: true })
        await runUntilIdle(combat)
        assert.deepEqual(bot.gotos, ['GoalInvert'])
    }
    assert.deepEqual(events.filter(e => e.type === 'activity_stuck'), [])
    activity.clearStack()
})

