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
