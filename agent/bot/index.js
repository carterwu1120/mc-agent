const mineflayer = require('mineflayer')
const minecraftProtocolForge = require('minecraft-protocol-forge')
const { pathfinder, Movements } = require('mineflayer-pathfinder')
const bridge = require('./bridge')
const { handle } = require('./commands')

const bot = mineflayer.createBot({
    host: 'localhost',
    port: 25565,
    username: 'Agent',
    version: '1.20.1',
    auth: 'offline',
    forgeHandshake: true,
})

minecraftProtocolForge.forgeHandshake(bot._client, { forge: true })
bot.loadPlugin(pathfinder)

bot.once('spawn', () => {
    console.log(`[Bot] 進入世界！位置：${JSON.stringify(bot.entity.position)}`)

    bot.pathfinder.setMovements(new Movements(bot))
    bridge.init(bot, (msg) => handle(bot, msg))

    setInterval(() => bridge.sendState(bot, 'tick'), 2000)
})

bot.once('health', () => {
    console.log(`[Bot] 血量：${bot.health}  飢餓：${bot.food}`)
})

bot.on('chat', (username, message) => {
    if (username === bot.username) return
    console.log(`[Chat] ${username}: ${message}`)

    if (message.startsWith('!')) {
        const [cmd, ...args] = message.slice(1).split(' ')
        handle(bot, { command: cmd, text: args.join(' '), args })
        return
    }

    bridge.sendState(bot, 'chat', { from: username, message })
})

bot.on('error', (err) => console.error(`[Error] ${err.message}`))
bot.on('kicked', (reason) => console.log(`[Kicked] ${reason}`))
bot.on('end', () => console.log('[Bot] 連線結束'))
