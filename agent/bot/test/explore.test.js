const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { Vec3 } = require('vec3')
const safeSpot = require('../safe_spot')

const loaded = { exports: {} }
vm.runInNewContext(fs.readFileSync(require.resolve('../explore'), 'utf8'), {
    module: loaded,
    require: name => ({
        './execution': { isCurrent: () => true, owned: fn => fn },
        './activity': { register: () => {} },
        './bridge': { sendState: () => {} },
        './crafting': { noteTeleportLikeAction: () => {} },
        './safe_spot': safeSpot,
    })[name] || require(name),
    console,
    setTimeout,
})
const { findExploreSpotForSurface } = loaded.exports

function block(name, position, boundingBox = 'empty') {
    return { name, position, boundingBox }
}

test('surface exploration accepts safe terrain below the bot current Y', () => {
    const base = new Vec3(0, 70, 0)
    const bot = {
        entity: { position: base },
        game: { minY: -64, height: 384, dimension: 'overworld' },
        world: { getSkyLight: () => 15 },
        blockAt(pos) {
            const p = pos.floored()
            if (p.y === 63) return block('grass_block', p, 'block')
            return block('air', p)
        },
    }

    const target = findExploreSpotForSurface(bot, 20, 20)

    assert.ok(target)
    assert.equal(target.y, 64)
    assert.equal(Math.round(target.distanceTo(base)), 21)
})

test('animal exploration from underground rejects dark cave floors and accepts distant surface height', () => {
    const bot = {
        entity: { position: new Vec3(0, 16, 0) },
        game: { minY: 0, height: 80, dimension: 'overworld' },
        world: { getSkyLight: p => p.y >= 64 ? 15 : 0 },
        blockAt(p) {
            if (p.y === 15 || p.y === 63) return block('stone', p, 'block')
            return block('air', p)
        },
    }
    assert.equal(findExploreSpotForSurface(bot, 20, 20, { targetType: 'animals' }).y, 64)
    bot.world.getSkyLight = () => 0
    assert.equal(findExploreSpotForSurface(bot, 20, 20, { targetType: 'animals' }), null)
})

test('failed teleport reports one movement failure and never claims no animals', async () => {
    const events = []
    let teleports = 0
    let pops = 0
    const finished = new Promise(resolve => {
        const module = { exports: {} }
        const bot = {
            username: 'Agent0', entity: { position: new Vec3(0, 16, 0) },
            game: { minY: 0, height: 80, dimension: 'overworld' },
            world: { getSkyLight: () => 15 }, pathfinder: { setGoal() {} },
            blockAt: p => block(p.y === 63 ? 'grass_block' : 'air', p, p.y === 63 ? 'block' : 'empty'),
            chat() { teleports++ },
        }
        const dependencies = {
            './execution': { isCurrent: () => true, owned: fn => fn },
            './activity': { register() {}, push() {}, pop() { pops++; resolve() } },
            './bridge': { sendState: (_bot, type, data) => events.push({ type, ...data }) },
            './crafting': { noteTeleportLikeAction() {} },
            './safe_spot': { ...safeSpot, waitForSafeLanding: async () => null },
        }
        vm.runInNewContext(fs.readFileSync(require.resolve('../explore'), 'utf8'), {
            module, require: name => dependencies[name] || require(name),
            console: { log() {} }, setTimeout,
        })
        module.exports.startExploring(bot, { target: 'animals', radius: 20, minRadius: 20 })
    })
    await finished
    assert.equal(teleports, 1)
    assert.equal(pops, 1)
    assert.equal(events.length, 1)
    assert.equal(events[0].reason, 'movement_failed')
    assert.equal(events[0].movement.position_unchanged, true)
})
