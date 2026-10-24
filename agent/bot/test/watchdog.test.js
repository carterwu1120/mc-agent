const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const activity = require('../activity')
const loaded = { exports: {} }
vm.runInNewContext(fs.readFileSync(require.resolve('../watchdog'), 'utf8'), {
    module: loaded, require: name => name === './activity' ? activity : {}, Date, setInterval,
})
const { ProgressWatch } = loaded.exports

function simulation(activityName = 'mining') {
    let now = 1000
    const watch = new ProgressWatch(() => now)
    const items = []
    const bot = { entity: { position: { x: 0, y: 64, z: 0 } }, inventory: { items: () => items } }
    const frame = { activity: activityName, startTime: 1000, goal: { count: 10 }, progress: { count: 0 } }
    const runtime = { running: true, paused: false, lastStateAt: now }
    const events = []
    const tick = (ms = 2000, options = {}) => {
        now += ms
        const e = watch.inspect(bot, frame, runtime, options)
        if (e) events.push(e)
        return e
    }
    tick(0)
    return { bot, frame, runtime, items, events, tick }
}

test('standing still is detected even with constantly refreshed touch timestamps', () => {
    const s = simulation()
    for (let n = 0; n < 15; n++) { s.runtime.lastProgressAt = Date.now(); s.tick() }
    assert.equal(s.events[0].reason, 'no_progress')
})

test('long operations emit throttled progress warnings before becoming stuck', () => {
    const s = simulation('chopping')
    const warnings = []
    for (let n = 0; n < 6; n++) {
        s.tick(2000, { onWarning: warning => warnings.push(warning) })
    }
    assert.equal(warnings.length, 1)
    assert.equal(warnings[0].activity, 'chopping')
})

test('oscillation, equipment churn and identical count do not count as progress', () => {
    const s = simulation()
    for (let n = 0; n < 25; n++) {
        s.bot.entity.position.x = n % 2 ? 2 : 0
        s.bot.heldItem = { name: n % 2 ? 'pickaxe' : 'dirt' }
        s.frame.progress.count = 0
        s.tick()
    }
    assert.equal(s.events.length, 1, 'duplicate reports are rate limited')
    assert.equal(s.events[0].reason, 'no_progress')
})

test('actual new positions allow slow travel, rather than requiring 1.25 blocks per poll', () => {
    const s = simulation('explore')
    for (let n = 0; n < 60; n++) { s.bot.entity.position.x += 0.4; s.tick() }
    assert.equal(s.events.length, 0)
})

test('new output permits long tasks, but inventory cycling does not', () => {
    const s = simulation()
    s.items.push({ name: 'cobblestone', count: 1 })
    for (let n = 0; n < 120; n++) { if (n % 8 === 0) s.items[0].count++; s.tick() }
    assert.equal(s.events.length, 0)
    for (let n = 0; n < 20; n++) { s.items[0].count = n % 2; s.tick() }
    assert.equal(s.events[0].reason, 'no_progress')
})

test('continued travel without resource output requests strategy review after 180s', () => {
    const s = simulation()
    for (let n = 0; n < 95; n++) { s.bot.entity.position.x += 2; s.tick() }
    assert.equal(s.events[0].reason, 'no_task_progress')
})

test('mining byproducts cannot indefinitely hide the absence of the requested ore', () => {
    const s = simulation()
    s.frame.goal.target = 'diamond'
    s.items.push({ name: 'cobblestone', count: 0 })
    for (let n = 0; n < 95; n++) { s.items[0].count++; s.tick() }
    assert.equal(s.events[0].reason, 'no_task_progress')
})

test('orphaned pause and expired expected waits are detected', () => {
    const s = simulation()
    s.runtime.paused = true
    s.runtime.running = false
    for (let n = 0; n < 24; n++) s.tick()
    assert.equal(s.events[0].reason, 'orphaned_pause')
    const waiting = simulation('smelting')
    waiting.runtime.wait = { reason: 'furnace', until: 61000 }
    for (let n = 0; n < 29; n++) waiting.tick()
    assert.equal(waiting.events.length, 0)
    assert.equal(waiting.tick().reason, 'wait_expired')
})

test('activity progress only advances on change, and repeated wait requests cannot renew a lease', () => {
    activity.clearStack()
    activity.push({}, 'test', {}, null)
    activity.updateProgress({ count: 0 })
    const before = activity.getRuntimeState('test').lastProgressAt
    activity.updateProgress({ count: 0 })
    assert.equal(activity.getRuntimeState('test').lastProgressAt, before)
    activity.waitFor('waiting', 1000)
    const until = activity.getRuntimeState('test').wait.until
    activity.waitFor('waiting', 100000)
    assert.equal(activity.getRuntimeState('test').wait.until, until)
    activity.clearStack()
})
