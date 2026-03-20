/**
 * world.js — 查詢 Minecraft 世界狀態的工具函數
 */

/**
 * 找最近的特定方塊
 * @param {object} bot
 * @param {string|string[]} blockName - 方塊名稱或名稱陣列
 * @param {number} maxRange - 搜尋半徑（預設 32）
 * @returns {object|null} 方塊物件或 null
 */
function findNearestBlock(bot, blockName, maxRange = 32) {
    const names = Array.isArray(blockName) ? blockName : [blockName]
    const blockIds = names
        .map(name => bot.registry.blocksByName[name]?.id)
        .filter(id => id !== undefined)

    if (blockIds.length === 0) return null

    return bot.findBlock({
        matching: blockIds,
        maxDistance: maxRange,
    })
}

/**
 * 找最近的水面（適合釣魚的位置）
 * @param {object} bot
 * @param {number} maxRange
 * @returns {object|null}
 */
function findNearestWater(bot, maxRange = 32) {
    return findNearestBlock(bot, ['water', 'flowing_water'], maxRange)
}

/**
 * 找適合釣魚的水方塊（水平距離 3-10 格，優先距離約 6 格）
 * @param {object} bot
 * @param {number} maxRange
 * @returns {object|null}
 */
function findFishableWater(bot, maxRange = 16) {
    const waterIds = ['water', 'flowing_water']
        .map(name => bot.registry.blocksByName[name]?.id)
        .filter(Boolean)

    if (waterIds.length === 0) return null

    // 收集範圍內所有水方塊
    const candidates = []
    const pos = bot.entity.position

    for (let dx = -maxRange; dx <= maxRange; dx++) {
        for (let dz = -maxRange; dz <= maxRange; dz++) {
            for (let dy = -4; dy <= 4; dy++) {
                const checkPos = pos.offset(dx, dy, dz)
                const block = bot.blockAt(checkPos)
                if (!block || !waterIds.includes(block.type)) continue

                const horzDist = Math.sqrt(dx * dx + dz * dz)
                // 水平距離要在 3-10 格之間（太近拋不到，太遠射程不夠）
                if (horzDist < 3 || horzDist > 10) continue

                candidates.push({ block, horzDist })
            }
        }
    }

    if (candidates.length === 0) return null

    candidates.sort((a, b) => {
        const idealDist = 6
        return Math.abs(a.horzDist - idealDist) - Math.abs(b.horzDist - idealDist)
    })

    return candidates[0]?.block ?? null
}

/**
 * 找最近的特定實體
 * @param {object} bot
 * @param {string} entityName - 實體名稱（e.g. 'fishing_bobber', 'cow'）
 * @returns {object|null}
 */
function findNearestEntity(bot, entityName) {
    return Object.values(bot.entities)
        .filter(e => e.id !== bot.entity.id && e.name === entityName && e.position)
        .sort((a, b) =>
            a.position.distanceTo(bot.entity.position) -
            b.position.distanceTo(bot.entity.position)
        )[0] ?? null
}

/**
 * 找最近的玩家
 * @param {object} bot
 * @returns {object|null}
 */
function findNearestPlayer(bot) {
    return Object.values(bot.entities)
        .filter(e => e.type === 'player' && e.id !== bot.entity.id && e.position)
        .sort((a, b) =>
            a.position.distanceTo(bot.entity.position) -
            b.position.distanceTo(bot.entity.position)
        )[0] ?? null
}

/**
 * 檢查某個位置是否是水
 * @param {object} bot
 * @param {object} position - Vec3 位置
 * @returns {boolean}
 */
function isWater(bot, position) {
    const block = bot.blockAt(position)
    return block?.name === 'water'
}

module.exports = {
    findNearestBlock,
    findNearestWater,
    findFishableWater,
    findNearestEntity,
    findNearestPlayer,
    isWater,
}
