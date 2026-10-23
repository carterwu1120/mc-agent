const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const {
    centeredTeleportPosition,
    isPassableSpace,
    isSafeGround,
    isSafeStandable,
    teleportCommand,
    waitForSafeLanding,
} = require('../safe_spot')

function block(name, boundingBox = 'empty') {
    return { name, boundingBox }
}

function botAt(position, blocks, skyLight = 15) {
    return {
        username: 'Agent0',
        entity: { position, onGround: true },
        blockAt(pos) {
            return blocks.get(pos.floored().toString()) ?? block('air')
        },
        world: { getSkyLight: () => skyLight },
    }
}

test('liquid and leaves are never accepted as body space or safe ground', () => {
    assert.equal(isPassableSpace(block('water')), false)
    assert.equal(isPassableSpace(block('oak_leaves', 'block')), false)
    assert.equal(isSafeGround(block('lava')), false)
    assert.equal(isSafeGround(block('oak_leaves', 'block')), false)
    assert.equal(isSafeGround(block('grass_block', 'block')), true)
})

test('standable position validates feet, head, ground and optional sky access', () => {
    const pos = new Vec3(4, 65, 7)
    const blocks = new Map([
        [pos.toString(), block('air')],
        [pos.offset(0, 1, 0).toString(), block('air')],
        [pos.offset(0, -1, 0).toString(), block('grass_block', 'block')],
    ])
    const bot = botAt(pos.offset(0.5, 0, 0.5), blocks)
    assert.equal(isSafeStandable(bot, pos, { requireSky: true }), true)
    blocks.set(pos.toString(), block('oak_leaves', 'block'))
    assert.equal(isSafeStandable(bot, pos), false)
})

test('teleport commands use the center of a block instead of its edge', () => {
    const target = new Vec3(-217, 69, 113)
    assert.deepEqual(centeredTeleportPosition(target), { x: -216.5, y: 69, z: 113.5 })
    assert.equal(teleportCommand('Agent0', target), '/tp Agent0 -216.5 69 113.5')
})

test('landing succeeds only after a stable safe position at the target', async () => {
    const target = new Vec3(2, 65, 3)
    const position = target.offset(0.5, 0, 0.5)
    const blocks = new Map([
        [target.toString(), block('air')],
        [target.offset(0, 1, 0).toString(), block('air')],
        [target.offset(0, -1, 0).toString(), block('stone', 'block')],
    ])
    const bot = botAt(position, blocks)
    const landed = await waitForSafeLanding(bot, target, {
        pollMs: 1,
        timeoutMs: 20,
        stableSamples: 2,
        sleep: async () => {},
    })
    assert.deepEqual(landed, target)
})

test('landing rejects a bot that fell below the selected target', async () => {
    const target = new Vec3(2, 69, 3)
    const fallen = new Vec3(2.5, 64, 3.5)
    const blocks = new Map([
        [fallen.floored().toString(), block('air')],
        [fallen.floored().offset(0, 1, 0).toString(), block('air')],
        [fallen.floored().offset(0, -1, 0).toString(), block('stone', 'block')],
    ])
    const bot = botAt(fallen, blocks)
    const landed = await waitForSafeLanding(bot, target, {
        pollMs: 1,
        timeoutMs: 5,
        stableSamples: 1,
        sleep: async () => {},
    })
    assert.equal(landed, null)
})
