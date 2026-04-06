const { WebSocketServer } = require('ws')
const { getActivity, getStack } = require('./activity')
const { getChests } = require('./chest')
const { getHome } = require('./home')
const { getMode } = require('./mode')

const TREE_BLOCK_NAMES = [
    'oak_log', 'spruce_log', 'birch_log', 'jungle_log',
    'acacia_log', 'dark_oak_log', 'mangrove_log', 'cherry_log',
]

function _hasNearbyBlock(bot, names, maxDistance = 8) {
    const ids = names.map(name => bot.registry.blocksByName[name]?.id).filter(Boolean)
    if (ids.length === 0) return false
    return !!bot.findBlock({ matching: ids, maxDistance })
}

function _equipmentState(bot) {
    return {
        main_hand: bot.heldItem?.name ?? null,
        off_hand: bot.inventory.slots[45]?.name ?? null,
        armor: {
            head: bot.inventory.slots[5]?.name ?? null,
            torso: bot.inventory.slots[6]?.name ?? null,
            legs: bot.inventory.slots[7]?.name ?? null,
            feet: bot.inventory.slots[8]?.name ?? null,
        },
    }
}

const wss = new WebSocketServer({ port: 3001 })
let agentSocket = null
let _initialized = false

function isInitialized() { return _initialized }

function init(bot, onAction) {
    _initialized = true
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
        mode: getMode(),
        activity: getActivity(),
        stack: getStack(),
        pos: bot.entity ? bot.entity.position : null,
        health: bot.health,
        food: bot.food,
        dimension: bot.game?.dimension ?? null,
        timeOfDay: bot.time?.timeOfDay ?? null,
        home: getHome(),
        equipment: _equipmentState(bot),
        nearby: {
            water: _hasNearbyBlock(bot, ['water'], 10),
            trees: _hasNearbyBlock(bot, TREE_BLOCK_NAMES, 10),
            stone: _hasNearbyBlock(bot, ['stone', 'cobblestone'], 8),
        },
        inventory: bot.inventory.items().map(i => ({ name: i.name, count: i.count })),
        chests: getChests(),
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

module.exports = { init, sendState, isInitialized }
