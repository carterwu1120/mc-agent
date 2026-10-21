const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { pathfinder, goals } = require('mineflayer-pathfinder')
const registry = require('minecraft-data')('1.20.1')
const Block = require('prismarine-block')(registry)
const { navigate, cancel, profiles, FailureLedger } = require('../navigation')

function world({ ground = () => true, inventory = [], corridor = false } = {}) {
    const bot = new EventEmitter()
    bot.registry = registry
    bot.entities = {}
    bot.entity = { position: new Vec3(0.5, 1, 0.5), effects: {}, onGround: true }
    bot.inventory = { items: () => inventory }
    bot.game = { dimension: 'overworld' }
    bot.clearControlStates = () => {}
    const edits = new Map()
    bot.blockAt = p => {
        p = p.floored()
        if (Math.abs(p.x) > 5 || Math.abs(p.z) > 3 || p.y < -3 || p.y > 4) return null
        const name = edits.get(p.toString()) ?? (
            corridor && Math.abs(p.z) >= 1 && p.y >= 0 ? 'bedrock' :
                corridor && p.y === 3 ? 'bedrock' :
                    p.y === 0 && ground(p.x, p.z) ? 'bedrock' : 'air')
        const block = Block.fromStateId(registry.blocksByName[name].minStateId, 0)
        block.position = p
        return block
    }
    bot.put = (x, y, z, name) => edits.set(new Vec3(x, y, z).toString(), name)
    pathfinder(bot)
    bot.executed = []
    bot.pathfinder.goto = async goal => {
        bot.pathfinder.setGoal(goal)
        const iterator = bot.pathfinder.getPathFromTo(bot.pathfinder.movements, bot.entity.position, goal, {
            optimizePath: false, timeout: 1000, tickTimeout: 1000, searchRadius: 16,
        })
        const { result } = iterator.next().value
        iterator.return()
        if (result.status !== 'success') throw new Error(`fixture path ${result.status}`)
        for (const node of result.path) {
            for (const p of node.toBreak) bot.put(p.x, p.y, p.z, 'air')
            for (const place of node.toPlace) {
                if (place.useOne) continue
                assert.equal(bot.blockAt(new Vec3(place.x, place.y, place.z))?.boundingBox, 'block')
                const item = inventory.find(i => profiles(bot)[2].movements.scafoldingBlocks.includes(i.type) && i.count > 0)
                assert.ok(item, 'placement consumes a real scaffold item')
                item.count--
                bot.put(place.x + place.dx, place.y + place.dy, place.z + place.dz, item.name)
            }
            bot.executed.push(node)
            bot.entity.position = new Vec3(node.x + 0.5, node.y, node.z + 0.5)
        }
    }
    return bot
}

test('real Pathfinder walks around an unbreakable wall', async () => {
    const bot = world()
    for (let y = 1; y <= 3; y++) bot.put(1, y, 0, 'bedrock')
    const result = await navigate(bot, new goals.GoalBlock(3, 1, 0))
    assert.equal(result.status, 'success')
    assert.ok(bot.executed.some(p => p.z !== 0))
    assert.ok(bot.executed.every(p => p.toBreak.length === 0 && p.toPlace.length === 0))
})

test('a blocked corridor switches from walking to a dig plan', async () => {
    const bot = world({ corridor: true, inventory: [{ name: 'iron_pickaxe', type: registry.itemsByName.iron_pickaxe.id, count: 1 }] })
    bot.put(1, 1, 0, 'stone')
    bot.put(1, 2, 0, 'stone')
    const result = await navigate(bot, new goals.GoalBlock(3, 1, 0))
    assert.equal(result.status, 'success', JSON.stringify(result))
    assert.ok(bot.executed.some(p => p.toBreak.length > 0))
})

test('a gap produces placement actions and consumes available blocks', async () => {
    const inventory = [{ name: 'cobblestone', type: registry.itemsByName.cobblestone.id, count: 3 }]
    const bot = world({ corridor: true, ground: x => x <= 0 || x >= 3, inventory })
    const result = await navigate(bot, new goals.GoalBlock(3, 1, 0))
    assert.equal(result.status, 'success')
    assert.ok(bot.executed.some(p => p.toPlace.length > 0))
    assert.equal(inventory[0].count, 1)
})

test('a gap without materials stops and remembers failed strategies across requests', async () => {
    const bot = world({ corridor: true, ground: x => x <= 0 || x >= 3 })
    const goal = new goals.GoalBlock(3, 1, 0)
    assert.equal((await navigate(bot, goal)).status, 'blocked')
    const second = await navigate(bot, goal)
    assert.equal(second.status, 'blocked')
    assert.ok(second.attempts.every(a => a.reason === 'recent_failure'))
    assert.equal(bot.executed.length, 0)
    // New materials change the observation; the old failure must not forbid a new plan.
    bot.inventory.items().push({ name: 'cobblestone', type: registry.itemsByName.cobblestone.id, count: 3 })
    assert.equal((await navigate(bot, goal)).status, 'success')
})

test('unknown terrain is not treated as a traversable floor', async () => {
    const bot = world({ corridor: true, ground: x => x === 0 })
    const blockAt = bot.blockAt
    bot.blockAt = p => p.x >= 1 ? null : blockAt(p)
    assert.equal((await navigate(bot, new goals.GoalBlock(3, 1, 0))).status, 'blocked')
    assert.equal(bot.executed.length, 0)
})

test('changed terrain invalidates the failed-route memory', async () => {
    const bot = world({ corridor: true, ground: x => x <= 0 || x >= 3 })
    const goal = new goals.GoalBlock(3, 1, 0)
    assert.equal((await navigate(bot, goal)).status, 'blocked')
    bot.put(1, 0, 0, 'cobblestone')
    bot.put(2, 0, 0, 'cobblestone')
    assert.equal((await navigate(bot, goal)).status, 'success')
})

test('a proposed placement without a real anchor is refused before execution', async () => {
    const bot = world({ corridor: true, ground: x => x <= 0 || x >= 3 })
    bot.pathfinder.getPathFromTo = function * () {
        yield { result: { status: 'success', path: [{ x: 2, y: 1, z: 0,
            toPlace: [{ x: 1, y: 0, z: 0, dx: 1, dy: 0, dz: 0 }],
        }] } }
    }
    const result = await navigate(bot, new goals.GoalBlock(3, 1, 0))
    assert.equal(result.status, 'blocked')
    assert.ok(result.attempts.every(a => a.reason === 'no_placement_anchor'))
    assert.equal(bot.executed.length, 0)
})

test('planner exceptions produce a bounded diagnostic instead of an unhandled rejection', async () => {
    const bot = world()
    bot.pathfinder.getPathFromTo = function * () { throw new Error('world changed') }
    const result = await navigate(bot, new goals.GoalBlock(2, 1, 0))
    assert.equal(result.status, 'blocked')
    assert.equal(result.attempts.length, 3)
    assert.ok(result.attempts.every(a => a.reason === 'planner_error'))
})

test('scaffold configuration uses inventory item IDs, not block IDs', () => {
    const bot = world({ inventory: [{ name: 'cobbled_deepslate', type: registry.itemsByName.cobbled_deepslate.id, count: 7 }] })
    assert.equal(profiles(bot)[2].movements.countScaffoldingItems(), 7)
    assert.equal(profiles(bot)[0].movements.countScaffoldingItems(), 0)
})

test('goto resolving without arrival is a failure, not success', async () => {
    const bot = world()
    bot.pathfinder.goto = async goal => bot.pathfinder.setGoal(goal)
    const result = await navigate(bot, new goals.GoalBlock(2, 1, 0))
    assert.equal(result.status, 'blocked')
    assert.ok(result.attempts.some(a => a.reason === 'arrival_not_verified'))
})

test('cancellation settles a hanging action without stopping a newer activity', async () => {
    const bot = world()
    let started
    const ready = new Promise(resolve => { started = resolve })
    bot.pathfinder.goto = goal => {
        bot.pathfinder.setGoal(goal)
        started()
        return new Promise(() => {})
    }
    const moving = navigate(bot, new goals.GoalBlock(2, 1, 0))
    await ready
    const newGoal = new goals.GoalBlock(-2, 1, 0)
    bot.pathfinder.setGoal(newGoal)
    cancel(bot)
    assert.equal((await moving).status, 'cancelled')
    assert.equal(bot.pathfinder.goal, newGoal)
})

test('hanging actions have a deadline', async () => {
    const bot = world()
    bot.pathfinder.goto = goal => {
        bot.pathfinder.setGoal(goal)
        return new Promise(() => {})
    }
    const result = await navigate(bot, new goals.GoalBlock(2, 1, 0), { timeoutMs: 60, segmentMs: 15 })
    assert.equal(result.status, 'blocked')
    assert.equal(bot.pathfinder.goal, null)
})

test('same-turn stack resume can navigate before the old cancellation settles', async () => {
    const bot = world()
    const execute = bot.pathfinder.goto
    let started
    const ready = new Promise(resolve => { started = resolve })
    bot.pathfinder.goto = goal => {
        bot.pathfinder.setGoal(goal)
        started()
        return new Promise(() => {})
    }
    const old = navigate(bot, new goals.GoalBlock(2, 1, 0))
    await ready
    cancel(bot)
    bot.pathfinder.goto = execute
    const resumed = navigate(bot, new goals.GoalBlock(-2, 1, 0))
    assert.equal((await old).status, 'cancelled')
    assert.equal((await resumed).status, 'success')
})

test('failure memory expires and has a size limit', () => {
    let time = 0
    const ledger = new FailureLedger({ ttl: 100, limit: 2, now: () => time })
    ledger.fail('a', 'blocked'); ledger.fail('b', 'blocked'); ledger.fail('c', 'blocked')
    assert.equal(ledger.has('a'), false)
    assert.equal(ledger.has('b'), true)
    time = 100
    assert.equal(ledger.has('b'), false)
})
