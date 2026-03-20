const { goals } = require('mineflayer-pathfinder')
const { startFishing, stopFishing } = require('./fishing')
const { findNearestPlayer } = require('./world')

function handle(bot, msg) {
    console.log('[Action]', JSON.stringify(msg))
    switch (msg.command) {
        case 'chat':
            bot.chat(msg.text)
            break

        case 'come': {
            const caller = msg.args?.[0]
            const player = caller
                ? bot.players[caller]?.entity
                : findNearestPlayer(bot)
            if (player) {
                bot.pathfinder.setGoal(new goals.GoalNear(player.position.x, player.position.y, player.position.z, 2))
                console.log(`[Action] 走向 ${player.username || 'player'}`)
            } else {
                console.log('[Action] 找不到玩家')
            }
            break
        }

        case 'look': {
            const name = msg.args?.[0]
            let target = name ? bot.players[name]?.entity : null
            if (!target) {
                target = Object.values(bot.entities)
                    .filter(e => e.id !== bot.entity.id && e.position)
                    .sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position))[0]
            }
            if (target) {
                bot.lookAt(target.position.offset(0, target.height ?? 1.6, 0))
                console.log(`[Action] 看向 ${target.name || target.username || target.id}`)
            } else {
                console.log('[Action] 找不到目標')
            }
            break
        }

        case 'fish':
            startFishing(bot)
            break

        case 'stopfish':
            stopFishing(bot)
            break

        case 'inv': {
            const items = bot.inventory.items()
            if (items.length === 0) {
                console.log('[Inv] 背包是空的')
            } else {
                items.forEach(i => console.log(`[Inv] ${i.name} x${i.count}`))
            }
            break
        }

        case 'clear': {
            const items = bot.inventory.items()
            if (items.length === 0) {
                console.log('[Inv] 背包已經是空的')
                break
            }
            ;(async () => {
                for (const item of items) {
                    await bot.tossStack(item)
                }
                console.log(`[Inv] 丟棄了 ${items.length} 種物品`)
            })()
            break
        }

        default:
            console.warn('[Action] 未知指令:', msg.command)
    }
}

module.exports = { handle }
