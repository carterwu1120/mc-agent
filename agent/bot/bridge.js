const { WebSocketServer } = require('ws')
const { getActivity, getStack } = require('./activity')

const wss = new WebSocketServer({ port: 3001 })
let agentSocket = null

function init(bot, onAction) {
    wss.on('connection', (ws) => {
        console.log('[WS] Agent 已連線')
        agentSocket = ws

        ws.on('message', (raw) => {
            try {
                const msg = JSON.parse(raw)
                onAction(msg)
            } catch (e) {
                console.error('[WS] 收到無效訊息:', raw)
            }
        })

        ws.on('close', () => {
            console.log('[WS] Agent 已斷線')
            agentSocket = null
        })
    })

    console.log('[WS] 等待 Agent 連線，port 3001')
}

function sendState(bot, type, extra = {}) {
    if (!agentSocket || agentSocket.readyState !== 1) return
    const state = {
        type,
        activity: getActivity(),
        stack: getStack(),
        pos: bot.entity ? bot.entity.position : null,
        health: bot.health,
        food: bot.food,
        inventory: bot.inventory.items().map(i => ({ name: i.name, count: i.count })),
        entities: Object.values(bot.entities)
            .filter(e => e.id !== bot.entity.id)
            .slice(0, 20)
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

module.exports = { init, sendState }
