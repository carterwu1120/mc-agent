const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')

const loaded = { exports: {} }
const stubs = {
    './execution': { isCurrent: () => true, owned: fn => fn },
    'mineflayer-pathfinder': { goals: {} },
    'vec3': { Vec3: class Vec3 {} },
    './activity': { register: () => {} },
    './crafting': {},
    './bridge': {},
    './surface': {},
    './movement_prefs': {},
}
vm.runInNewContext(fs.readFileSync(require.resolve('../woodcutting'), 'utf8'), {
    module: loaded,
    require: name => stubs[name],
    console,
    setTimeout,
})
const { _test } = loaded.exports

function botWithLogs(count) {
    return {
        inventory: {
            items: () => count > 0 ? [{ name: 'oak_log', count }] : [],
        },
    }
}

test('chopping goal counts collected inventory, not blocks merely dug', () => {
    const baseline = 3
    assert.equal(_test._collectedLogCount(botWithLogs(3), baseline), 0)
    assert.equal(_test._hasCollectedGoal(botWithLogs(3), baseline, { logs: 2 }), false)
    assert.equal(_test._collectedLogCount(botWithLogs(5), baseline), 2)
    assert.equal(_test._hasCollectedGoal(botWithLogs(5), baseline, { logs: 2 }), true)
})
