const mineflayer = require('mineflayer')
const minecraftProtocolForge = require('minecraft-protocol-forge')
const { WebSocketServer } = require('ws')

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

// ── 事件：成功進入世界 ─────────────────────────────────────
bot.once('spawn', () => {
    console.log(`[Bot] 進入世界！位置：${JSON.stringify(bot.entity.position)}`)
    console.log(`[WS] 等待 Agent 連線，port 3001`)

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
