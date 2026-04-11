const mineflayer = require('mineflayer')
const minecraftProtocolForge = require('minecraft-protocol-forge')
const { pathfinder } = require('mineflayer-pathfinder')
const { initLogger } = require('./logger')
const bridge = require('./bridge')
const activityStack = require('./activity')
const { handle } = require('./commands')
const eating = require('./eating')
const inventory = require('./inventory')
const combat = require('./combat')
const water = require('./water')
const watchdog = require('./watchdog')
const { applyMovements } = require('./movement_prefs')

initLogger('bot')

const bot = mineflayer.createBot({
    host: process.env.MC_HOST || 'localhost',
    port: parseInt(process.env.MC_PORT || '25565'),
    username: process.env.MC_USERNAME || 'Agent',
    version: process.env.MC_VERSION || '1.20.1',
    auth: process.env.MC_AUTH || 'offline',
    forgeHandshake: true,
})

minecraftProtocolForge.forgeHandshake(bot._client, { forge: true })
bot.loadPlugin(pathfinder)

function _pathfinderCallerLabel() {
    const stack = new Error().stack?.split('\n').slice(2) ?? []
    for (const line of stack) {
        const match = line.match(/agent[\\/]+bot[\\/]+([^:\\)\s]+):(\d+)/i)
        if (!match) continue
        if (match[1].toLowerCase() === 'index.js') continue
        return `${match[1]}:${match[2]}`
    }
    return 'unknown'
}

function _wrapPathfinderDebug(bot) {
    if (!bot.pathfinder || bot.pathfinder.__debugWrapped) return

    const originalGoto = bot.pathfinder.goto.bind(bot.pathfinder)
    const originalSetGoal = bot.pathfinder.setGoal.bind(bot.pathfinder)
    const originalBestHarvestTool = typeof bot.pathfinder.bestHarvestTool === 'function'
        ? bot.pathfinder.bestHarvestTool.bind(bot.pathfinder)
        : null

    bot.pathfinder.goto = function wrappedGoto(goal, dynamic) {
        const caller = _pathfinderCallerLabel()
        const goalName = goal?.constructor?.name ?? 'UnknownGoal'
        const target = [goal?.x, goal?.y, goal?.z].filter(v => v !== undefined).join(', ')
        console.log(`[Path] goto by ${caller} -> ${goalName}${target ? ` (${target})` : ''}`)
        return originalGoto(goal, dynamic).catch((err) => {
            console.log(`[Path] goto failed for ${caller}: ${err.message}`)
            throw err
        })
    }

    bot.pathfinder.setGoal = function wrappedSetGoal(goal, dynamic) {
        const caller = _pathfinderCallerLabel()
        const goalName = goal?.constructor?.name ?? 'null'
        const target = goal ? [goal?.x, goal?.y, goal?.z].filter(v => v !== undefined).join(', ') : ''
        console.log(`[Path] setGoal by ${caller} -> ${goalName}${target ? ` (${target})` : ''}`)
        return originalSetGoal(goal, dynamic)
    }

    if (originalBestHarvestTool) {
        bot.pathfinder.bestHarvestTool = function wrappedBestHarvestTool(block) {
            if (!block) return null
            try {
                return originalBestHarvestTool(block)
            } catch (err) {
                console.log(`[Path] bestHarvestTool failed: ${err.message}`)
                return null
            }
        }
    }

    bot.pathfinder.__debugWrapped = true
}

bot.once('spawn', () => {
    console.log(`[Bot] 進入世界！位置：${JSON.stringify(bot.entity.position)}`)

    _wrapPathfinderDebug(bot)
    applyMovements(bot)
    bridge.init(bot, (msg) => handle(bot, msg))
    eating.startMonitor(bot)
    inventory.startMonitor(bot)
    combat.startMonitor(bot)
    water.startMonitor(bot)
    watchdog.startMonitor(bot)
    ;(async () => { await combat.equipArmor(bot); await combat.equipWeapon(bot) })()

    setInterval(() => bridge.sendState(bot, 'tick'), 2000)

    let _lastDurabilityWarnAt = 0
    setInterval(() => {
        const now = Date.now()
        if (now - _lastDurabilityWarnAt < 60000) return
        const slots = [
            bot.heldItem,
            bot.inventory.slots[5],  // helmet
            bot.inventory.slots[6],  // chestplate
            bot.inventory.slots[7],  // leggings
            bot.inventory.slots[8],  // boots
        ]
        const lowItems = []
        for (const item of slots) {
            if (!item || !item.maxDurability) continue
            const pct = Math.max(0, Math.round(((item.maxDurability - item.durabilityUsed) / item.maxDurability) * 100))
            if (pct <= 10) lowItems.push({ item: item.name, durability_pct: pct })
        }
        if (lowItems.length === 0) return
        _lastDurabilityWarnAt = now
        console.log(`[Durability] 耐久度警告：${lowItems.map(i => `${i.item} ${i.durability_pct}%`).join(', ')}`)
        bridge.sendState(bot, 'tool_low_durability', { items: lowItems })
    }, 5000)

    // Register respawn listener only after first join, so it won't fire on initial spawn
    bot.on('spawn', () => {
        _pendingDeathInfo = null
        console.log(`[Bot] 重生！位置：${JSON.stringify(bot.entity.position)}`)
        bridge.sendState(bot, 'player_respawned', { spawnPos: bot.entity.position })
    })
})

bot.once('health', () => {
    console.log(`[Bot] 血量：${bot.health}  飢餓：${bot.food}`)
})

// Usernames of other bots in the same server — ignore their chat to prevent feedback loops.
// Bot-to-bot coordination happens via the Python coordinator layer, not Minecraft chat.
const _BOT_USERNAMES = new Set(
    (process.env.BOT_USERNAMES || '').split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
)
for (const name of Array.from(_BOT_USERNAMES)) {
    const base = name.replace(/\d+$/, '')
    if (base && base !== name) _BOT_USERNAMES.add(base)
}

function _matchesAddress(target, username) {
    const normalizedTarget = (target || '').trim().toLowerCase()
    const normalizedUsername = (username || '').trim().toLowerCase()
    if (!normalizedTarget || !normalizedUsername) return false
    if (normalizedTarget === normalizedUsername) return true
    const baseUsername = normalizedUsername.replace(/\d+$/, '')
    return !!baseUsername && normalizedTarget === baseUsername
}

bot.on('chat', (username, message) => {
    if (username === bot.username) return
    if (_BOT_USERNAMES.has(username.toLowerCase())) return  // ignore other bots
    console.log(`[Chat] ${username}: ${message}`)

    // Addressing: "@Agent0 mine iron 8"  → only Agent0 responds
    //             "@all sethome"          → all bots respond
    //             (no prefix)             → all bots respond (backward-compatible)
    const addressMatch = message.match(/^@(\S+)\s+([\s\S]*)$/)
    if (addressMatch) {
        const [, target, rest] = addressMatch
        if (target.toLowerCase() !== 'all' &&
            !_matchesAddress(target, bot.username)) return
        message = rest.trim()
    }

    if (message.startsWith('!')) {
        const [cmd, ...args] = message.slice(1).split(' ')
        handle(bot, { command: cmd, text: args.join(' '), args })
        return
    }

    if (message.startsWith('/')) return  // Minecraft client command — ignore

    bridge.sendState(bot, 'chat', { from: username, message })
})

let _pendingDeathInfo = null

bot.on('health', () => {
    if (bot.health > 0 || _pendingDeathInfo !== null) return

    const stack = activityStack.getStack()
    // Use bottom frame (original long-running task), not top (which may be combat/transient)
    const baseFrame = stack.length > 0 ? stack[0] : null
    const topFrame = stack.length > 0 ? stack[stack.length - 1] : null
    _pendingDeathInfo = {
        cause: bot.entity?.isInLava ? 'lava' : (bot.entity?.isInWater ? 'drowning' : 'other'),
        deathPos: bot.entity?.position ? { ...bot.entity.position } : null,
        startPos: baseFrame?.startPos ?? null,
        lastActivity: baseFrame?.activity ?? null,
        lastGoal: baseFrame?.goal ?? null,
    }
    console.log(`[Bot] 死亡！原因：${_pendingDeathInfo.cause}，startPos：${JSON.stringify(_pendingDeathInfo.startPos)}`)
    bridge.sendState(bot, 'player_died', _pendingDeathInfo)
})

bot.on('error', (err) => console.error(`[Error] ${err.message}`))
bot.on('kicked', (reason) => console.log(`[Kicked] ${reason}`))
bot.on('end', () => console.log('[Bot] 連線結束'))
