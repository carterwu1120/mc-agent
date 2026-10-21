const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const execution = require('../execution')
const activity = require('../activity')

test('ordinary stack pause revokes the old scope and resume preserves the original command id', async () => {
    activity.clearStack()
    const effects = []
    const bot = { pathfinder: { setGoal: goal => effects.push(goal) } }
    execution.install(bot)
    let release
    const startParent = execution.owned(async () => {
        activity.register('parent', () => {})
        activity.push(bot, 'parent', {}, () => {
            assert.equal(execution.commandId(), 'original-command')
            bot.pathfinder.setGoal('resumed')
        })
        await new Promise(resolve => { release = resolve })
        bot.pathfinder.setGoal('stale-parent')
        activity.pop(bot)
    })
    const parent = execution.run(() => startParent(), 'original-command')
    execution.run(() => activity.push(bot, 'child', {}, null), 'child-command')
    release()
    await parent
    assert.equal(activity.getActivity(), 'child')
    activity.pop(bot)
    assert.deepEqual(effects, ['resumed'])
    activity.clearStack()
})

test('superseded continuations cannot move, dig, or pop the replacement activity', async () => {
    activity.clearStack()
    const calls = []
    const bot = { pathfinder: { setGoal: goal => calls.push(goal) }, dig: async () => calls.push('dig') }
    execution.install(bot)
    let release
    const old = execution.run(async () => {
        await new Promise(resolve => { release = resolve })
        bot.pathfinder.setGoal('old')
        activity.pop(bot)
        await assert.rejects(bot.dig({}), { name: 'AbortError' })
    }, 'old')
    execution.invalidate()
    execution.run(() => { activity.push(bot, 'replacement', {}, null); bot.pathfinder.setGoal('new') }, 'new')
    release()
    await old
    assert.deepEqual(calls, ['new'])
    assert.equal(activity.getActivity(), 'replacement')
    activity.clearStack()
})

test('completion of an operation already in flight is rejected after suspension', async () => {
    let finish
    const bot = { dig: () => new Promise(resolve => { finish = resolve }) }
    execution.install(bot)
    const operation = execution.run(() => bot.dig({}))
    await Promise.resolve()
    execution.invalidate()
    finish()
    await assert.rejects(operation, { name: 'AbortError' })
})

test('suspend acknowledges only after clearing all frames and physical controls', () => {
    activity.clearStack()
    const effects = []
    const bridge = { sendState(_bot, type, extra) { effects.push({ type, ...extra }) } }
    const bot = { pathfinder: { setGoal: () => effects.push('goal_clear') }, stopDigging: () => effects.push('dig_stop'), clearControlStates: () => effects.push('controls_clear') }
    const deps = {
        './execution': execution, './activity': activity, './bridge': bridge,
        './inventory': { checkFull: () => false, cancelRecovery() {} },
        './fishing': { stopFishing() {} },
        './navigation': { cancel() {} }, './logger': { setTaskId() {} },
    }
    const mod = { exports: {} }
    vm.runInNewContext(fs.readFileSync(require.resolve('../commands'), 'utf8'), {
        module: mod, require: name => deps[name] || {}, console: { log() {}, warn() {} },
    })
    activity.register('first', () => effects.push('pause_first'))
    activity.register('second', () => effects.push('pause_second'))
    activity.push(bot, 'first', {}, () => effects.push('MUST_NOT_RESUME'))
    activity.push(bot, 'second', {}, null)
    effects.length = 0
    mod.exports.handle(bot, { command: 'suspend', request_id: 'request-1' })
    assert.equal(activity.getActivity(), 'idle')
    assert.equal(effects.includes('MUST_NOT_RESUME'), false)
    assert.equal(effects.at(-1).type, 'recovery_paused')
    assert.equal(effects.at(-1).request_id, 'request-1')
    assert.equal(effects.at(-1).stopped, true)
    assert.ok(effects.indexOf('dig_stop') < effects.length - 1)
    activity.push(bot, 'first', {}, () => effects.push('MUST_NOT_RESUME'))
    activity.push(bot, 'fishing', {}, null)
    mod.exports.handle(bot, { command: 'stopfish' })
    assert.equal(activity.getActivity(), 'idle', 'manual stop must remove paused parent frames')
    assert.equal(effects.includes('MUST_NOT_RESUME'), false)
})
