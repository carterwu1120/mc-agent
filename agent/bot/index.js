const mineflayer = require('mineflayer')
const minecraftProtocolForge = require('minecraft-protocol-forge')
const { WebSocketServer } = require('ws')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')

// ── WebSocket Server（給 Python Agent 連） ────────────────
const wss = new WebSocketServer({ port: 3001 })
let agentSocket = null  // 目前連線的 agent

wss.on('connection', (ws) => {
    console.log('[WS] Agent 已連線')
    agentSocket = ws

    ws.on('message', (raw) => {
        try {
            const msg = JSON.parse(raw)
            handleAction(msg)
        } catch (e) {
            console.error('[WS] 收到無效訊息:', raw)
        }
    })

    ws.on('close', () => {
        console.log('[WS] Agent 已斷線')
        agentSocket = null
    })
})

// ── 傳送 state 給 Agent ───────────────────────────────────
function sendState(type, extra = {}) {
    if (!agentSocket || agentSocket.readyState !== 1) return
    const state = {
        type,
        pos: bot.entity ? bot.entity.position : null,
        health: bot.health,
        food: bot.food,
        inventory: bot.inventory.items().map(i => ({ name: i.name, count: i.count })),
        entities: Object.values(bot.entities)
            .filter(e => e.id !== bot.entity.id)
            .slice(0, 20)  // 最多送 20 個實體，避免資料太大
            .map(e => ({
                id: e.id,
                name: e.name || e.username,
                type: e.type,
                pos: e.position,
                distance: bot.entity.position.distanceTo(e.position),
            })),
        ...extra,
    }
    agentSocket.send(JSON.stringify(state))
}

// ── 處理 Agent 傳來的 action ──────────────────────────────
function handleAction(msg) {
    console.log('[Action]', JSON.stringify(msg))
    switch (msg.command) {
        case 'chat':
            bot.chat(msg.text)
            break
        case 'move':
            // 之後實作移動邏輯
            console.log('[Action] move 尚未實作')
            break
        case 'come': {
            // 找發指令的玩家或最近的玩家
            const caller = msg.args?.[0]
            const player = caller
                ? bot.players[caller]?.entity
                : Object.values(bot.entities).find(e => e.type === 'player' && e.id !== bot.entity.id)
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
            let target = null
            if (name) {
                // 指定名字：找玩家
                target = bot.players[name]?.entity
            }
            if (!target) {
                // 沒指定或找不到：找最近的實體
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
            // 之後實作釣魚邏輯
            console.log('[Action] fish 尚未實作')
            break
        default:
            console.warn('[Action] 未知指令:', msg.command)
    }
}

// ── 建立 bot ──────────────────────────────────────────────
const bot = mineflayer.createBot({
    host: 'localhost',
    port: 25565,
    username: 'Agent',
    version: '1.20.1',
    auth: 'offline',
    forgeHandshake: true,
})

// Forge 握手支援
minecraftProtocolForge.forgeHandshake(bot._client, { forge: true })
bot.loadPlugin(pathfinder)

// ── 事件：成功進入世界 ─────────────────────────────────────
bot.once('spawn', () => {
    console.log(`[Bot] 進入世界！位置：${JSON.stringify(bot.entity.position)}`)
    console.log(`[WS] 等待 Agent 連線，port 3001`)

    const movements = new Movements(bot)
    bot.pathfinder.setMovements(movements)

    // 每 2 秒送一次 state 給 Agent
    setInterval(() => sendState('tick'), 2000)
})

bot.once('health', () => {
    console.log(`[Bot] 血量：${bot.health}  飢餓：${bot.food}`)
})

// ── 事件：收到聊天訊息 ─────────────────────────────────────
bot.on('chat', (username, message) => {
    if (username === bot.username) return
    console.log(`[Chat] ${username}: ${message}`)

    // 手動指令（以 ! 開頭），直接執行不走 LLM
    if (message.startsWith('!')) {
        const [cmd, ...args] = message.slice(1).split(' ')
        handleAction({ command: cmd, text: args.join(' '), args })
        return
    }

    sendState('chat', { from: username, message })
})

// ── 事件：錯誤處理 ────────────────────────────────────────
bot.on('error', (err) => {
    console.error(`[Error] ${err.message}`)
})

bot.on('kicked', (reason) => {
    console.log(`[Kicked] ${reason}`)
})

bot.on('end', () => {
    console.log('[Bot] 連線結束')
})
