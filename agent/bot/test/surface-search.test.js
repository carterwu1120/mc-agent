const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { Vec3 } = require('vec3')

function loadSurface() {
    const module = { exports: {} }
    const stubs = {
        './execution': require('../execution'),
        './activity': { register() {} },
        './bridge': { sendActivityDone() {}, sendState() {} },
        './crafting': { noteTeleportLikeAction() {} },
        './movement_prefs': { applyMovements() {} },
        './safe_spot': require('../safe_spot'),
    }
    vm.runInNewContext(fs.readFileSync(require.resolve('../surface'), 'utf8'), {
        module, exports: module.exports, require: name => stubs[name] || require(name), console: { log() {} },
    })
    return module.exports
}

// A world from y=-64 to 319: solid stone up to `groundY(x, z)`, then either water or
// open sky above. Chunk columns expose 16-block sections with isEmpty().
function makeBot(position, groundY, isWater = () => false) {
    const blockAt = p => {
        const top = groundY(p.x, p.z)
        let name
        if (p.y <= top) name = 'stone'
        else if (isWater(p.x, p.z) && p.y <= 62) name = 'water'
        else name = 'air'
        return { name, position: p, boundingBox: name === 'stone' ? 'block' : 'empty' }
    }
    const column = pos => ({
        minY: -64,
        sections: Array.from({ length: 24 }, (_, i) => {
            const bottom = -64 + i * 16
            const highest = Math.max(groundY(pos.x, pos.z), isWater(pos.x, pos.z) ? 62 : -999)
            return { isEmpty: () => bottom > highest }
        }),
    })
    return {
        entity: { position },
        game: { dimension: 'overworld', minY: -64, height: 384 },
        blockAt,
        world: {
            getColumnAt: column,
            // Sky light is full only above the topmost non-air block of the column.
            getSkyLight: p => (p.y > Math.max(groundY(p.x, p.z), isWater(p.x, p.z) ? 62 : -999) ? 15 : 0),
        },
    }
}

test('the column straight above an underground bot is a candidate', () => {
    const { findSurfaceSpot } = loadSurface()
    const bot = makeBot(new Vec3(0, -35, 0), () => 70)
    assert.deepEqual(findSurfaceSpot(bot).toArray(), [0, 71, 0])
})

test('under a lake wider than 24 blocks, land further out is still found', () => {
    const { findSurfaceSpot } = loadSurface()
    const lake = (x, z) => Math.hypot(x, z) < 40
    const bot = makeBot(new Vec3(0, -35, 0), (x, z) => (lake(x, z) ? 40 : 65), lake)
    const spot = findSurfaceSpot(bot)
    assert.ok(spot, 'land beyond the lake is found')
    assert.equal(spot.y, 66)
    assert.ok(Math.hypot(spot.x, spot.z) >= 40)
})

test('ground exactly at a section boundary is not skipped', () => {
    const { findSurfaceSpot } = loadSurface()
    // Stone up to y=63, so feet y=64 is the lowest block of the empty section 64..79.
    const bot = makeBot(new Vec3(0, -35, 0), () => 63)
    assert.equal(findSurfaceSpot(bot).y, 64)
})
