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

module.exports = { findNearestBlock, findNearestWater, findNearestEntity, findNearestPlayer }
