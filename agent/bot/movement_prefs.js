const { Movements } = require('mineflayer-pathfinder')

function createMovements(bot, options = {}) {
    const {
        canDig = undefined,
        allowWater = false,
        scaffoldBlockNames = null,
        canDigBlock = null,
    } = options

    const movements = new Movements(bot)

    if (canDig !== undefined) movements.canDig = canDig
    if (typeof canDigBlock === 'function') movements.canDigBlock = canDigBlock

    if (Array.isArray(scaffoldBlockNames)) {
        movements.scafoldingBlocks = scaffoldBlockNames
            .map(n => bot.registry.blocksByName[n]?.id)
            .filter(id => id !== undefined)
    }

    if (!allowWater) {
        if ('liquidCost' in movements) movements.liquidCost = 50
        if ('waterCost' in movements) movements.waterCost = 50
        if ('canSwim' in movements) movements.canSwim = false
        if ('dontCreateFlow' in movements) movements.dontCreateFlow = true

        const waterIds = ['water', 'flowing_water']
            .map(name => bot.registry.blocksByName[name]?.id)
            .filter(id => id !== undefined)

        if (waterIds.length > 0) {
            if (movements.blocksToAvoid instanceof Set) {
                for (const id of waterIds) movements.blocksToAvoid.add(id)
            }
            if (movements.liquids instanceof Set) {
                for (const id of waterIds) movements.liquids.add(id)
            }
        }
    }

    return movements
}

function applyMovements(bot, options = {}) {
    const movements = createMovements(bot, options)
    bot.pathfinder.setMovements(movements)
    return movements
}

module.exports = { createMovements, applyMovements }
