const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')

function loadCombat() {
    const module = { exports: {} }
    const stubs = {
        './activity': require('../activity'),
        './execution': require('../execution'),
        './bridge': { sendState() {} },
        './crafting': { ensureCraftingTable: async () => ({ name: 'crafting_table' }), reclaimCraftingTable: async () => {} },
    }
    vm.runInNewContext(fs.readFileSync(require.resolve('../combat'), 'utf8'), {
        module, exports: module.exports, require: name => stubs[name] || require(name),
        console: { log() {} }, setTimeout, clearTimeout, Date,
    })
    return module.exports
}

// Results come from another vm context, so compare them as plain JSON.
const plain = value => JSON.parse(JSON.stringify(value))

const COST = { helmet: 5, chestplate: 8, leggings: 7, boots: 4 }
const SLOT = { head: 5, torso: 6, legs: 7, feet: 8 }

// Inventory of { name: count }; slots 5-8 hold worn armor. craft consumes the material.
function makeBot(stock, worn = {}) {
    const slots = new Array(46).fill(null)
    for (const [dest, name] of Object.entries(worn)) slots[SLOT[dest]] = { name, maxDurability: 100, durabilityUsed: 0 }
    const bot = {
        crafted: [],
        inventory: {
            slots,
            items: () => Object.entries(stock).filter(([, n]) => n > 0).map(([name, count]) => ({ name, count })),
        },
        registry: { itemsByName: new Proxy({}, { get: (_t, name) => ({ id: name }) }) },
        recipesFor: id => [{ id }],
        craft: async recipe => {
            const [material, piece] = recipe.id.split('_')
            const item = material === 'iron' ? 'iron_ingot' : 'diamond'
            if ((stock[item] ?? 0) < COST[piece]) throw new Error('missing ingredients')
            stock[item] -= COST[piece]
            stock[recipe.id] = 1
            bot.crafted.push(recipe.id)
        },
        equip: async (item, dest) => { slots[SLOT[dest]] = { name: item.name, maxDurability: 100, durabilityUsed: 0 }; stock[item.name] = 0 },
    }
    return bot
}

test('makearmor crafts and wears a full iron set from carried ingots', async () => {
    const combat = loadCombat()
    const bot = makeBot({ iron_ingot: 24 })
    const result = await combat.makeArmor(bot, 'iron')
    assert.deepEqual(plain(result), { crafted: ['iron_helmet', 'iron_chestplate', 'iron_leggings', 'iron_boots'], missing: 0 })
    assert.deepEqual([5, 6, 7, 8].map(s => bot.inventory.slots[s].name),
        ['iron_helmet', 'iron_chestplate', 'iron_leggings', 'iron_boots'])
})

test('makearmor keeps better pieces and reports the shortfall instead of gathering', async () => {
    const combat = loadCombat()
    const bot = makeBot({ iron_ingot: 10 }, { head: 'diamond_helmet', torso: 'leather_chestplate' })
    const result = await combat.makeArmor(bot, 'iron')
    // needs chestplate 8 + leggings 7 + boots 4 = 19; 10 ingots make the chestplate only
    assert.deepEqual(plain(result), { crafted: ['iron_chestplate'], missing: 9 })
    assert.equal(bot.inventory.slots[5].name, 'diamond_helmet')
})

test('makearmor rejects an unknown material', async () => {
    const combat = loadCombat()
    await assert.rejects(combat.makeArmor(makeBot({}), 'gold'), /invalid_material/)
})
