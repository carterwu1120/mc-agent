const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { Vec3 } = require('vec3')

function loadMining(water = { isEscaping: () => false }) {
    const stubs = {
        './mining_phase': require('../mining_phase'), './activity': require('../activity'),
        './execution': require('../execution'), './crafting': {}, './bridge': { sendState() {} },
        './buried': {}, './eating': { isEating: () => false }, './water': water,
        './hazards': { isNear: () => false }, './movement_prefs': { applyMovements: () => ({}) },
        './navigation': { enabled: () => false, cancel() {}, lastResult: () => null },
    }
    const context = vm.createContext({ module: { exports: {} }, require: name => stubs[name] || require(name),
        console: { log() {} }, process, setTimeout, clearTimeout, Date })
    vm.runInContext(fs.readFileSync(require.resolve('../mining'), 'utf8'), context)
    vm.runInContext('isMining = true', context) // script-level `let`, shared within the context
    return context
}

// Ore blocks keyed by "x,y,z"; the bot stands in air at the origin and dug blocks
// become air; everything else is solid deepslate. `lava` marks lava blocks.
function makeBot(ores, lava = [], { air = [], items = [{ name: 'iron_pickaxe', count: 1 }] } = {}) {
    const key = p => `${p.x},${p.y},${p.z}`
    const oreSet = new Set(ores.map(key))
    const lavaSet = new Set(lava.map(key))
    const airSet = new Set(['0,-58,0', '0,-57,0', ...air.map(key)])
    const placed = []
    const dug = []
    const blockAt = p => {
        const k = key(p)
        if (oreSet.has(k)) return { name: 'deepslate_diamond_ore', position: p, boundingBox: 'block' }
        if (lavaSet.has(k)) return { name: 'lava', position: p, boundingBox: 'empty' }
        if (airSet.has(k)) return { name: 'air', position: p, boundingBox: 'empty' }
        return { name: 'deepslate', position: p, boundingBox: 'block' }
    }
    return {
        dug,
        placed,
        entity: { position: new Vec3(0, -58, 0) },
        entities: {},
        inventory: { items: () => items },
        placeBlock: async (ref, face) => {
            const p = ref.position.plus(face)
            airSet.delete(key(p)); placed.push(key(p))
        },
        findBlocks: ({ matching }) => [...oreSet].map(k => new Vec3(...k.split(',').map(Number)))
            .filter(p => matching(blockAt(p))),
        blockAt,
        dig: async block => { oreSet.delete(key(block.position)); airSet.add(key(block.position)); dug.push(key(block.position)) },
        pathfinder: { goto: async () => {}, setGoal() {}, setMovements() {}, bestHarvestTool: () => null },
        equip: async () => {},
    }
}

test('the rest of the vein next to the bot is mined before stopping', async () => {
    // Seen live: "目標 3/3" reached, then the bot stopped in front of more diamonds.
    const mining = loadMining()
    const bot = makeBot([new Vec3(1, -58, 0), new Vec3(1, -57, 0), new Vec3(2, -58, 0)])
    await mining._finishVein(bot, { target: 'diamond', count: 3 }, null)
    assert.equal(bot.dug.length, 3)
})

test('stone targets and lava-adjacent ore are not chased', async () => {
    const mining = loadMining()
    const stone = makeBot([new Vec3(1, -58, 0)])
    await mining._finishVein(stone, { target: 'stone', count: 3 }, null)
    assert.deepEqual(stone.dug, [])

    const nearLava = makeBot([new Vec3(1, -58, 0)], [new Vec3(2, -58, 0)])
    await mining._finishVein(nearLava, { target: 'diamond', count: 3 }, null)
    assert.deepEqual(nearLava.dug, [])
})

test('buried ore is not seen: only exposed ore is mined (no x-ray)', async () => {
    const mining = loadMining()
    // (1,-58,0) touches the air the bot stands in; (5,-58,0) is sealed in rock.
    const bot = makeBot([new Vec3(1, -58, 0), new Vec3(5, -58, 0)])
    await mining._finishVein(bot, { target: 'diamond', count: 3 }, null)
    assert.deepEqual(bot.dug, ['1,-58,0'])
})


test('mining does not dig while a hazard escape is digging the bot out', async () => {
    // Seen live: tunnel digs and the suffocation escape kept aborting each other
    // ("Digging aborted") until falling gravel killed the bot.
    let escaping = true
    const mining = loadMining({ isEscaping: () => escaping })
    const bot = makeBot([new Vec3(1, -58, 0)])
    const run = mining._finishVein(bot, { target: 'diamond', count: 1 }, null)
    await new Promise(r => setTimeout(r, 300))
    assert.deepEqual(bot.dug, [], 'waits for the escape')
    escaping = false
    await run
    assert.deepEqual(bot.dug, ['1,-58,0'])
})

test('a stair landing over a cave gets a placed floor instead of giving up', async () => {
    // Seen live: above a cave all four stair directions had no floor, so the descent
    // stopped while the bot carried 128 building blocks.
    const mining = loadMining()
    const hole = new Vec3(1, -60, 0)
    const bot = makeBot([], [], { air: [hole], items: [{ name: 'iron_pickaxe', count: 1 }, { name: 'cobbled_deepslate', count: 64 }] })
    await mining._stairDown(bot, -Math.PI / 2, 1)
    assert.deepEqual(bot.placed, ['1,-60,0'])
    assert.ok(bot.dug.includes('1,-59,0'), 'then digs the stair step')

    const noBlocks = makeBot([], [], { air: [hole] })
    await mining._stairDown(noBlocks, -Math.PI / 2, 1)
    assert.deepEqual([noBlocks.placed, noBlocks.dug], [[], []], 'without blocks it still refuses the hole')
})

// The bot stands at (0,-58,0) on rock; `air` lists cave blocks. goto moves the bot onto the goal.
function caveBot(air) {
    const bot = makeBot([], [], { air })
    bot.pathfinder.goto = async goal => { bot.entity.position = new Vec3(goal.x, goal.y, goal.z) }
    return bot
}
const box = (x0, x1, y0, y1) => {
    const blocks = []
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) blocks.push(new Vec3(x, y, 0))
    return blocks
}

test('standing on a thin layer over a cave, the bot walks down into the cave', async () => {
    // Seen live: every stair direction had a hole under it and the task stopped at y=-20.
    const mining = loadMining()
    const shallow = caveBot(box(1, 3, -61, -59))           // floor at y=-62, top of it y=-61
    assert.equal(await mining._walkDownCave(shallow, -100), true)
    assert.equal(shallow.entity.position.y, -61)

    const deep = caveBot(box(1, 6, -70, -59))              // a 12-block-deep cave
    assert.equal(await mining._walkDownCave(deep, -100), true)
    assert.equal(deep.entity.position.y, -70, 'goes to the lowest floor, not just the first drop')
})

test('with no cave floor in reach the walk fails so the descent shifts sideways', async () => {
    const mining = loadMining()
    const solid = caveBot([])
    assert.equal(await mining._walkDownCave(solid, -100), false)
    assert.deepEqual(solid.entity.position, new Vec3(0, -58, 0))
})
