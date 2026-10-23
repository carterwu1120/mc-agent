const PASSABLE = new Set([
    'air', 'cave_air', 'void_air',
    'grass', 'short_grass', 'tall_grass', 'fern', 'large_fern',
    'vine', 'weeping_vines', 'twisting_vines',
])

const UNSAFE_GROUND = new Set([
    'lava', 'water', 'fire', 'soul_fire', 'magma_block', 'cactus',
    'campfire', 'soul_campfire', 'powder_snow', 'sweet_berry_bush',
])

function isPassableSpace(block) {
    if (!block?.name) return false
    if (UNSAFE_GROUND.has(block.name)) return false
    if (block.name.includes('leaves')) return false
    if (block.boundingBox && block.boundingBox !== 'empty') return false
    return PASSABLE.has(block.name) || block.boundingBox === 'empty'
}

function isSafeGround(block) {
    if (!block?.name || UNSAFE_GROUND.has(block.name)) return false
    if (block.name.includes('leaves')) return false
    if (PASSABLE.has(block.name)) return false
    return block.boundingBox ? block.boundingBox === 'block' : true
}

function skyLightAt(bot, pos) {
    try {
        return bot.world?.getSkyLight?.(pos) ?? 0
    } catch (_) {
        return 0
    }
}

function isSafeStandable(bot, pos, options = {}) {
    if (!pos) return false
    const feet = pos.floored ? pos.floored() : pos
    const feetBlock = bot.blockAt(feet)
    const headBlock = bot.blockAt(feet.offset(0, 1, 0))
    const ground = bot.blockAt(feet.offset(0, -1, 0))
    if (!isPassableSpace(feetBlock) || !isPassableSpace(headBlock)) return false
    if (!isSafeGround(ground)) return false
    return !options.requireSky || skyLightAt(bot, feet) >= (options.minSkyLight ?? 14)
}

function centeredTeleportPosition(pos) {
    return { x: pos.x + 0.5, y: pos.y, z: pos.z + 0.5 }
}

function teleportCommand(username, pos) {
    const centered = centeredTeleportPosition(pos)
    return `/tp ${username} ${centered.x} ${centered.y} ${centered.z}`
}

function _sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms))
}

async function waitForSafeLanding(bot, target, options = {}) {
    const timeoutMs = options.timeoutMs ?? 3000
    const pollMs = options.pollMs ?? 100
    const requiredStableSamples = options.stableSamples ?? 3
    const centered = centeredTeleportPosition(target)
    const started = Date.now()
    let last = null
    let stableSamples = 0

    while (Date.now() - started < timeoutMs) {
        const pos = bot.entity?.position
        if (pos) {
            const horizontalDistance = Math.hypot(pos.x - centered.x, pos.z - centered.z)
            const nearTarget = horizontalDistance <= (options.horizontalTolerance ?? 0.9)
                && Math.abs(pos.y - centered.y) <= (options.verticalTolerance ?? 0.75)
            const stable = last
                && Math.abs(pos.x - last.x) < 0.03
                && Math.abs(pos.y - last.y) < 0.03
                && Math.abs(pos.z - last.z) < 0.03

            stableSamples = stable ? stableSamples + 1 : 0
            last = { x: pos.x, y: pos.y, z: pos.z }

            if (nearTarget
                && stableSamples >= requiredStableSamples
                && bot.entity.onGround !== false
                && isSafeStandable(bot, pos, options)) {
                return pos.floored ? pos.floored() : pos
            }
        }
        await (options.sleep ?? _sleep)(pollMs)
    }
    return null
}

module.exports = {
    centeredTeleportPosition,
    isPassableSpace,
    isSafeGround,
    isSafeStandable,
    skyLightAt,
    teleportCommand,
    waitForSafeLanding,
}
