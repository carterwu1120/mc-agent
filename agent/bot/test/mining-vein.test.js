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
function makeBot(ores, lava = []) {
    const key = p => `${p.x},${p.y},${p.z}`
    const oreSet = new Set(ores.map(key))
    const lavaSet = new Set(lava.map(key))
    const airSet = new Set(['0,-58,0', '0,-57,0'])
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
        entity: { position: new Vec3(0, -58, 0) },
        entities: {},
        inventory: { items: () => [{ name: 'iron_pickaxe', count: 1 }] },
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
