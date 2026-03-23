const { goals } = require('mineflayer-pathfinder')
const { startFishing, stopFishing, applyLLMDecision } = require('./fishing')
const { applyInventoryDecision } = require('./inventory')
const { startChopping, stopChopping } = require('./woodcutting')
const { startMining, stopMining } = require('./mining')
const { startSmelting, stopSmelting } = require('./smelting')
const { applyCraftDecision } = require('./crafting')
const { equipBestLoadout, equipSpecific, unequipAll, unequipSpecific } = require('./equipment')
const { findNearestPlayer } = require('./world')

function handle(bot, msg) {
    console.log('[Action]', JSON.stringify(msg))
    switch (msg.command) {
        case 'chat':
            if (msg.text) bot.chat(msg.text)
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
            startFishing(bot, msg.goal ?? _parseGoal(msg.args, ['catches', 'duration']))
            break

        case 'stopfish':
            stopFishing(bot)
            break

        case 'chop':
            startChopping(bot, msg.goal ?? _parseGoal(msg.args, ['logs', 'duration']))
            break

        case 'stopchop':
            stopChopping(bot)
            break

        case 'mine':
            startMining(bot, msg.goal ?? _parseMineGoal(msg.args))
            break

        case 'stopmine':
            stopMining(bot)
            break

        case 'smelt':
            startSmelting(bot, msg.goal ?? _parseSmeltGoal(msg.args))
            break

        case 'stopsmelt':
            stopSmelting(bot)
            break

        case 'smeltout': {
            // 測試用：找最近的熔爐，印出 slot 狀態，嘗試取出
            ;(async () => {
                const furnaceId    = bot.registry.blocksByName['furnace']?.id
                const litFurnaceId = bot.registry.blocksByName['lit_furnace']?.id
                const block = bot.findBlock({
                    matching: b => b.type === furnaceId || (litFurnaceId && b.type === litFurnaceId),
                    maxDistance: 8,
                })
                if (!block) { console.log('[SmeltOut] 找不到熔爐'); return }
                console.log(`[SmeltOut] 找到熔爐 at ${block.position}`)
                let furnace
                try {
                    furnace = await bot.openFurnace(block)
                    await new Promise(r => setTimeout(r, 500))
                } catch (e) { console.log('[SmeltOut] openFurnace 失敗:', e.message); return }
                console.log(`[SmeltOut] fuel(progress)=${furnace.fuel} slots[0]=${JSON.stringify(furnace.slots[0]?.name)} slots[1]=${JSON.stringify(furnace.slots[1]?.name)} slots[2]=${JSON.stringify(furnace.slots[2]?.name)}`)
                const outputItem = furnace.slots[2]
                if (outputItem) {
                    try {
                        await furnace.takeOutput()
                        console.log(`[SmeltOut] 取出成功: ${outputItem.name} x${outputItem.count}`)
                    } catch (e) {
                        console.log('[SmeltOut] takeOutput 失敗:', e.message)
                    }
                } else {
                    console.log('[SmeltOut] slots[2] 是空的')
                }
                furnace.close()
            })()
            break
        }

        case 'fishing_decision':
            applyLLMDecision(msg)
            break

        case 'inventory_decision':
            applyInventoryDecision(msg)
            break

        case 'craft_decision':
            applyCraftDecision(msg)
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

        case 'equip':
            ;(async () => {
                const target = msg.args?.[0]
                if (target) {
                    const equipped = await equipSpecific(bot, target)
                    console.log(`[Equip] 單件裝備結果：${equipped ?? '無'}`)
                    return
                }

                const result = await equipBestLoadout(bot)
                console.log(`[Equip] 完成裝備：武器=${result.weapon ?? '無'}，護甲=${result.armor?.join(', ') || '無'}`)
            })()
            break

        case 'unequip':
            ;(async () => {
                const target = msg.args?.[0]
                const removed = target
                    ? await unequipSpecific(bot, target)
                    : await unequipAll(bot)
                console.log(`[Equip] 完成卸裝：${removed.join(', ') || '無'}`)
            })()
            break

        case 'tp': {
            const args = msg.args ?? []
            if (args.length >= 3 && args.slice(0, 3).every(a => !isNaN(a))) {
                // !tp x y z
                const [x, y, z] = args.map(Number)
                bot.chat(`/tp ${bot.username} ${x} ${y} ${z}`)
                console.log(`[Action] 傳送到座標 ${x} ${y} ${z}`)
            } else if (args.length >= 1) {
                // !tp playername
                bot.chat(`/tp ${bot.username} ${args[0]}`)
                console.log(`[Action] 傳送到玩家 ${args[0]}`)
            } else {
                // !tp → 傳送到最近的玩家
                const player = findNearestPlayer(bot)
                if (player) {
                    bot.chat(`/tp ${bot.username} ${player.username}`)
                    console.log(`[Action] 傳送到 ${player.username}`)
                } else {
                    console.log('[Action] 找不到玩家')
                }
            }
            break
        }

        case 'bury': {
            const { buryItems } = require('./inventory')
            // 解析 args：'cobblestone 32 diorite' → Map { cobblestone→32, diorite→null }
            const items = new Map()
            const args = msg.args ?? []
            for (let i = 0; i < args.length; i++) {
                const count = parseInt(args[i + 1], 10)
                if (!isNaN(count)) {
                    items.set(args[i], count)
                    i++
                } else {
                    items.set(args[i], null)
                }
            }
            buryItems(bot, items)
            break
        }

        case 'tidy': {
            const { tidyInventory } = require('./inventory')
            tidyInventory(bot)
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

// 從 chat args 解析 goal，e.g. ['logs', '20'] → { logs: 20 }
function _parseGoal(args, validKeys) {
    if (!args || args.length < 2) return {}
    const key = args[0]
    const val = parseInt(args[1], 10)
    if (validKeys.includes(key) && !isNaN(val)) return { [key]: val }
    return {}
}

// mine 的 goal 格式：['iron', '20'] → { target: 'iron', count: 20 }，['duration', '300'] → { duration: 300 }
function _parseMineGoal(args) {
    if (!args || args.length === 0) return {}
    if (args[0] === 'duration' && args[1]) return { duration: parseInt(args[1], 10) }
    if (args.length >= 2) return { target: args[0], count: parseInt(args[1], 10) }
    return {}
}

// smelt 的 goal 格式：['iron', '20'] → { target: 'iron', count: 20 }，['duration', '300'] → { duration: 300 }，['iron'] → { target: 'iron' }
function _parseSmeltGoal(args) {
    if (!args || args.length === 0) return {}
    if (args[0] === 'duration' && args[1]) return { duration: parseInt(args[1], 10) }
    if (args.length >= 2) return { target: args[0], count: parseInt(args[1], 10) }
    return { target: args[0] }
}

module.exports = { handle }
