const test = require('node:test')
const assert = require('node:assert/strict')
const activity = require('../activity')

const bot = { entity: { position: { x: 0, y: 64, z: 0 } } }
const names = () => activity.getStack().map(f => f.activity)
let resumed = []
activity.register('smelting', () => {})
activity.register('mining', () => {})

test('a frame that reported done is replaced by the next activity, never resumed', () => {
    // Seen live: smelting reported done, the next command pushed mining while smelting
    // was still reclaiming its furnace, and smelting stayed under mining forever.
    activity.clearStack(); resumed = []
    activity.push(bot, 'smelting', {}, () => resumed.push('smelting'))
    activity.markFinished('smelting')
    activity.push(bot, 'mining', {}, () => resumed.push('mining'))
    assert.deepEqual(names(), ['mining'])
    activity.pop(bot)
    assert.deepEqual(names(), [])
    assert.deepEqual(resumed, [])
})

test('an unfinished frame is still paused under the new activity and resumed after it', () => {
    activity.clearStack(); resumed = []
    activity.push(bot, 'mining', {}, () => resumed.push('mining'))
    activity.push(bot, 'smelting', {}, () => resumed.push('smelting'))
    assert.deepEqual(names(), ['mining', 'smelting'])
    activity.pop(bot)
    assert.deepEqual(resumed, ['mining'])
    activity.clearStack()
})
