const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')

const events = []
let canDeliver = true
const loaded = { exports: {} }
vm.runInNewContext(fs.readFileSync(require.resolve('../eating'), 'utf8'), {
    module: loaded,
    require: name => {
        if (name === './bridge') {
            return {
                sendState: (_bot, type) => {
                    if (!canDeliver) return false
                    events.push(type)
                    return true
                },
            }
        }
        if (name === './activity') return { getActivity: () => 'idle' }
        return require(name)
    },
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
})
const { _checkFoodLow, _resetFoodLowStateForTest } = loaded.exports

test('food_low is emitted only when the observable low-food state changes', () => {
    events.length = 0
    canDeliver = false
    const bot = {
        food: 9,
        inventory: { items: () => [] },
    }

    try {
        _resetFoodLowStateForTest()
        _checkFoodLow(bot)
        assert.deepEqual(events, [])

        canDeliver = true
        _checkFoodLow(bot)
        _checkFoodLow(bot)
        assert.deepEqual(events, ['food_low'])

        bot.food = 8
        _checkFoodLow(bot)
        assert.deepEqual(events, ['food_low', 'food_low'])

        bot.food = 10
        _checkFoodLow(bot)
        bot.food = 9
        _checkFoodLow(bot)
        assert.deepEqual(events, ['food_low', 'food_low', 'food_low'])
    } finally {
        canDeliver = true
        _resetFoodLowStateForTest()
    }
})
