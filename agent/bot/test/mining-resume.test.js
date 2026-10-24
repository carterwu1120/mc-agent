const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { Vec3 } = require('vec3')
const activity = require('../activity')

test('mining keeps executing after 60 seconds instead of requesting a timed decision', async () => {
    activity.clearStack()
    let now = 1000
    const requests = []
    const events = []
    const bot = {
        entity: { position: new Vec3(0, 64, 0), yaw: 0 },
        inventory: { items: () => [{ name: 'iron_pickaxe', count: 1 }] },
        pathfinder: { setGoal() {} }, clearControlStates() {}, stopDigging() {},
        findBlocks() { return [this.entity.position.floored().offset(1, 0, 0)] },
        blockAt(p) {
            const target = this.entity.position.floored().offset(1, 0, 0)
            return { name: p.equals(target) ? 'stone' : 'air', position: p,
                boundingBox: p.equals(target) ? 'block' : 'empty' }
        },
    }
    const stubs = {
        './mining_phase': require('../mining_phase'), './activity': activity,
        './execution': require('../execution'), './crafting': {},
        './bridge': { sendState: (_bot, type) => events.push(type) },
        './buried': {}, './eating': { isEating: () => false }, './water': {},
        './hazards': { isNear: () => false }, './movement_prefs': { applyMovements: () => ({}) },
        './navigation': { enabled: () => true, cancel() {}, lastResult: () => null,
            navigate: (_bot, goal, options) => new Promise(resolve => requests.push({ goal, options, resolve })) },
    }
    const module = { exports: {} }
    vm.runInNewContext(fs.readFileSync(require.resolve('../mining'), 'utf8'), {
        module, exports: module.exports, require: name => stubs[name] || require(name),
        Date: class extends Date { static now() { return now } },
        console: { log() {} }, process, setTimeout, clearTimeout,
    })
    const mining = module.exports
    const flush = () => new Promise(resolve => setImmediate(resolve))
    try {
        await mining.startMining(bot, { target: 'stone', count: 3 })
        assert.equal(requests.length, 1)
        now += 240000
        bot.entity.position.x = 2
        requests[0].resolve({ status: 'success' })
        await flush()
        assert.equal(requests.length, 2, 'next mining attempt continues after elapsed time')
        assert.equal(events.includes('mining_decision'), false)
        assert.equal(activity.getActivity(), 'mining')
    } finally {
        mining.stopMining(bot)
        for (const request of requests) request.resolve({ status: 'cancelled', reason: 'cancelled' })
        await flush()
        activity.clearStack()
    }
})

test('stack resume replans from the current location; stale mining cannot dig or pop the new frame', async () => {
    activity.clearStack()
    const requests = []
    const bot = {
        entity: { position: new Vec3(0, 64, 0), yaw: 0 },
        inventory: { items: () => [{ name: 'iron_pickaxe', count: 1 }] },
        entities: {}, registry: { blocksByName: {} },
        pathfinder: { setGoal() {} }, clearControlStates() {}, stopDigging() {},
        findBlocks() { return [this.entity.position.floored().offset(1, 0, 0)] },
        blockAt(p) {
            const target = this.entity.position.floored().offset(1, 0, 0)
            return { name: p.equals(target) ? 'stone' : 'air', position: p, boundingBox: p.equals(target) ? 'block' : 'empty' }
        },
        digs: 0,
        async dig() { this.digs++ },
    }
    const stubs = {
        './mining_phase': require('../mining_phase'),
        './activity': activity,
        './execution': require('../execution'),
        './crafting': { ensureToolFor: async () => true, ensurePickaxeTier: async () => true, ensurePickaxe: async () => true },
        './bridge': { sendState() {}, sendActivityDone() {} },
        './buried': { isBuried: () => false },
        './eating': { isEating: () => false },
        './water': { isEscaping: () => false },
        './hazards': { isNear: () => false },
        './movement_prefs': { applyMovements: () => ({}) },
        './navigation': {
            enabled: () => true, cancel() {}, lastResult: () => null,
            navigate(_bot, goal, options) {
                return new Promise(resolve => requests.push({ goal, options, resolve }))
            },
        },
    }
    const module = { exports: {} }
    vm.runInNewContext(fs.readFileSync(require.resolve('../mining'), 'utf8'), {
        module, exports: module.exports,
        require: name => stubs[name] || require(name),
        console: { log() {} }, process, setTimeout, clearTimeout,
    }, { filename: 'mining.js' })
    const mining = module.exports
    const flush = () => new Promise(resolve => setImmediate(resolve))
    try {
        await mining.startMining(bot, { target: 'stone', count: 3 })
        assert.equal(requests.length, 1)
        activity.push(bot, 'test_interrupt', {}, null)
        bot.entity.position = new Vec3(100, 64, 0)
        activity.pop(bot)
        assert.equal(requests.length, 2)
        assert.equal(requests[1].goal.x, 101, 'do not navigate back to the old resumePos')
        assert.equal(requests[0].options.shouldContinue(), false, 'old loop generation is invalid')
        requests[0].resolve({ status: 'success' })
        await flush()
        assert.equal(bot.digs, 0)
        assert.equal(activity.getActivity(), 'mining', 'old loop cannot pop the resumed activity')
    } finally {
        mining.stopMining(bot)
        for (const request of requests) request.resolve({ status: 'cancelled', reason: 'cancelled' })
        await flush()
        activity.clearStack()
    }
})
