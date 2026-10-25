const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')

const ITEMS = ['stick', 'cobblestone', 'raw_iron', 'iron_ingot', 'crafting_table', 'oak_planks',
    'wooden_pickaxe', 'stone_pickaxe', 'iron_pickaxe', 'diamond_pickaxe']

// A bot standing next to a crafting table that can craft only the given items.
function makeBot(inventory, craftable) {
    const itemsByName = Object.fromEntries(ITEMS.map((name, id) => [name, { id, name }]))
    const names = Object.fromEntries(ITEMS.map((name, id) => [id, name]))
    const table = { name: 'crafting_table', position: { x: 1, y: 64, z: 0 } }
    return {
        registry: { itemsByName, blocksByName: { crafting_table: { id: 100 }, furnace: { id: 101 } } },
        inventory: { items: () => inventory },
        // `matching` is either a block id or a predicate over blocks.
        findBlock: ({ matching }) => {
            const isTable = typeof matching === 'function' ? matching({ type: 100 }) : matching === 100
            return isTable ? table : null
        },
        pathfinder: { goto: async () => {} },
        recipesFor: id => (craftable.includes(names[id]) ? [{ id }] : []),
        craft: async recipe => { inventory.push({ name: names[recipe.id], count: 1 }) },
        equip: async () => {},
    }
}

function loadCrafting(smelting) {
    const module = { exports: {} }
    const stubs = {
        'mineflayer-pathfinder': { goals: { GoalNear: class {} } },
        vec3: { Vec3: class {} },
        './smelting': smelting,
        './bridge': { sendState() {} },
    }
    vm.runInNewContext(fs.readFileSync(require.resolve('../crafting'), 'utf8'), {
        module, exports: module.exports, require: name => stubs[name] || require(name),
        console: { log() {} }, setTimeout, clearTimeout,
    })
    return module.exports
}

function smeltingStub({ active = false } = {}) {
    const calls = []
    return {
        calls,
        startSmelting: (_bot, goal) => calls.push(goal),
        isActive: () => active,
        consumeLastOutcome: () => null,
    }
}

test('an explicit tier is crafted directly instead of detouring through iron smelting', async () => {
    const smelting = smeltingStub()
    const crafting = loadCrafting(smelting)
    const inventory = [{ name: 'stick', count: 2 }, { name: 'cobblestone', count: 9 }, { name: 'raw_iron', count: 3 }]
    const bot = makeBot(inventory, ['stone_pickaxe'])

    assert.equal(await crafting.ensurePickaxeTier(bot, 'stone_pickaxe', true), true)
    assert.deepEqual(smelting.calls, [], 'preparetool stone_pickaxe must not start smelting')
    assert.ok(inventory.some(i => i.name === 'stone_pickaxe'))
})

test('a tool request from inside active smelting does not start smelting again', { timeout: 5000 }, async () => {
    // Seen live: smelting needed a pickaxe to clear a furnace spot, the pickaxe request
    // tried to smelt iron again, and both waited on each other.
    const smelting = smeltingStub({ active: true })
    const crafting = loadCrafting(smelting)
    const inventory = [{ name: 'stick', count: 2 }, { name: 'cobblestone', count: 9 }, { name: 'raw_iron', count: 3 }]
    const bot = makeBot(inventory, ['stone_pickaxe'])

    assert.equal(await crafting.ensureToolFor(bot, 'stone'), true)
    assert.deepEqual(smelting.calls, [])
})

test('allowSmelt=false crafts only from carried materials', async () => {
    const smelting = smeltingStub()
    const crafting = loadCrafting(smelting)
    const inventory = [{ name: 'stick', count: 2 }, { name: 'cobblestone', count: 9 }, { name: 'raw_iron', count: 3 }]
    const bot = makeBot(inventory, [])

    assert.equal(await crafting.ensurePickaxeTier(bot, 'iron_pickaxe', true, true, false), false)
    assert.deepEqual(smelting.calls, [], 'mining replacement must not smelt raw ore')
})

test('one raw iron is not smelted toward a three-ingot tool', async () => {
    // Seen live: a tool request smelted the only raw_iron, smelting then ran out of
    // input and reported no_input, which exhausted the task's recovery budget.
    const smelting = smeltingStub()
    const crafting = loadCrafting(smelting)
    const inventory = [{ name: 'stick', count: 2 }, { name: 'cobblestone', count: 9 }, { name: 'raw_iron', count: 1 }]
    const bot = makeBot(inventory, [])

    assert.equal(await crafting.ensurePickaxeTier(bot, 'iron_pickaxe', true), false)
    assert.deepEqual(smelting.calls, [])
})
