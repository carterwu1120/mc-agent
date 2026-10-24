const test = require('node:test')
const assert = require('node:assert/strict')
const { equipBestLoadout, equipSpecific } = require('../equipment')

function fakeBot(items = []) {
    const equips = []
    const slots = []
    const destinations = { head: 5, torso: 6, legs: 7, feet: 8, 'off-hand': 45 }
    return {
        equips,
        heldItem: null,
        inventory: {
            slots,
            items: () => items,
        },
        getEquipmentDestSlot: slot => destinations[slot],
        equip: async (item, slot) => { equips.push([item.name, slot]) },
        // A pure equip operation must never inspect recipes or registry items.
        get registry() { throw new Error('equip attempted to craft') },
        recipesFor() { throw new Error('equip attempted to craft') },
    }
}

test('equip with no owned gear does not craft or place a workbench', async () => {
    const bot = fakeBot()

    const result = await equipBestLoadout(bot)

    assert.deepEqual(result, { armor: [], weapon: null })
    assert.deepEqual(bot.equips, [])
})

test('equip selects only armor and weapons already in inventory', async () => {
    const items = [
        { name: 'iron_helmet', count: 1 },
        { name: 'wooden_sword', count: 1 },
    ]
    const bot = fakeBot(items)

    const result = await equipBestLoadout(bot)

    assert.deepEqual(result, { armor: ['iron_helmet'], weapon: 'wooden_sword' })
    assert.deepEqual(bot.equips, [
        ['iron_helmet', 'head'],
        ['wooden_sword', 'hand'],
    ])
})

test('specific armor slot does not craft when inventory has no armor', async () => {
    const bot = fakeBot()

    const result = await equipSpecific(bot, 'head')

    assert.equal(result, null)
    assert.deepEqual(bot.equips, [])
})
