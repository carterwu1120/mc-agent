#!/usr/bin/env node
// mcctl — command-line controller that lets a coding agent drive the mineflayer bot.
//
// The bot (scripts/bot) is a WebSocket server that pushes state events and accepts
// command messages. A short-lived CLI cannot hold that socket, so `start` launches a
// local daemon which owns the bot process and the socket, buffers non-tick events and
// exposes a loopback-only HTTP API. Every other subcommand is a thin HTTP client.
'use strict'
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const net = require('node:net')
const http = require('node:http')
const { spawn, spawnSync } = require('node:child_process')
const { createRequire } = require('node:module')

const BOT_DIR = _resolveBotDir()
const MCBOT_HOME = process.env.MCBOT_HOME || path.join(os.homedir(), '.mcbot')

// How each bot command reports completion (mirrors the dispatch in bot/commands.js).
const ACTIVITY = new Set(['fish', 'chop', 'mine', 'smelt', 'combat', 'hunt', 'getfood', 'surface', 'explore'])
const ACTION_DONE = new Set([
    'stopfish', 'stopchop', 'stopmine', 'stopsmelt', 'stopcombat', 'stophunt', 'stopgetfood',
    'stopsurface', 'stopexplore', 'stopall', 'sethome', 'home', 'back', 'makechest', 'setchest',
    'labelchest', 'readchest', 'deposit', 'withdraw', 'preparetool', 'equip', 'unequip', 'tp', 'setmode',
])
const NO_ACK = new Set(['chat', 'come', 'look', 'inv', 'bury', 'tidy'])
const TERMINAL = new Set(['action_done', 'activity_done', 'activity_stuck', 'mining_decision', 'recovery_paused'])
const FAILED = new Set(['activity_stuck', 'mining_decision'])
// Events which need the agent's attention even while another command is running.
const INTERRUPT = new Set([
    'activity_stuck', 'mining_decision', 'inventory_full', 'craft_decision', 'chat', 'player_died', 'food_low',
    'bot_exited', 'mc_disconnected',
])
// Recorded in the event log but never wake `wait` on their own.
const ROUTINE = new Set(['task_started', 'task_stopped', 'mining_progress'])
const BASE_KEYS = new Set([
    'type', 'command_id', 'mode', 'activity', 'stack', 'activity_runtime', 'navigation', 'mc_connected',
    'pos', 'health', 'food', 'dimension', 'timeOfDay', 'home', 'equipment', 'nearby', 'inventory',
    'inventory_slots', 'chests', 'entities',
])
const MAX_EVENTS = 500

function _resolveBotDir() {
    const candidates = [
        process.env.MCBOT_BOT_DIR,
        path.join(__dirname, 'bot'),       // packaged skill: scripts/mcctl.js + scripts/bot
        path.join(__dirname, '..', 'bot'), // repository: agent/skill/mcctl.js + agent/bot
    ].filter(Boolean)
    return candidates.find(dir => fs.existsSync(path.join(dir, 'index.js'))) ?? candidates[candidates.length - 1]
}

// ── Argument parsing ─────────────────────────────────────────

function parseArgs(argv) {
    const flags = {}
    const positional = []
    for (let i = 0; i < argv.length; i++) {
        const arg = argv[i]
        if (!arg.startsWith('--')) { positional.push(arg); continue }
        const [key, inline] = arg.slice(2).split('=', 2)
        if (inline !== undefined) flags[key] = inline
        else if (argv[i + 1] !== undefined && !argv[i + 1].startsWith('--')) flags[key] = argv[++i]
        else flags[key] = true
    }
    return { flags, positional }
}

function stateDir(name) {
    if (!/^[A-Za-z0-9_-]{1,32}$/.test(name)) throw new Error('--name must be 1-32 letters, digits, _ or -')
    return path.join(MCBOT_HOME, name)
}

function readJson(file) {
    try { return JSON.parse(fs.readFileSync(file, 'utf8')) } catch { return null }
}

function isAlive(pid) {
    if (!pid) return false
    try { process.kill(pid, 0); return true } catch { return false }
}

function out(obj, code = 0) {
    process.stdout.write(JSON.stringify(obj) + '\n')
    process.exitCode = code
}

// ── State compaction ─────────────────────────────────────────

const _round = v => Math.round(v * 10) / 10

function _itemLabel(item) {
    if (!item) return null
    return item.durability_pct !== undefined ? `${item.name}@${item.durability_pct}%` : item.name
}

function compactState(s) {
    if (!s) return null
    const inventory = {}
    for (const item of s.inventory ?? []) {
        const key = _itemLabel(item)
        inventory[key] = (inventory[key] ?? 0) + item.count
    }
    const pos = s.pos ? { x: Math.round(s.pos.x), y: Math.round(s.pos.y), z: Math.round(s.pos.z) } : null
    const nearbyEntities = (s.entities ?? [])
        .filter(e => (e.type === 'player' || e.type === 'hostile') && e.distance <= 24)
        .sort((a, b) => a.distance - b.distance)
        .slice(0, 8)
        .map(e => `${e.type === 'player' ? 'player' : 'hostile'}:${e.name}@${Math.round(e.distance)}m`)
    const armor = Object.values(s.equipment?.armor ?? {}).filter(Boolean).map(_itemLabel)
    const result = {
        activity: s.activity,
        stack: (s.stack ?? []).map(f => ({ activity: f.activity, goal: f.goal, progress: f.progress })),
        pos,
        dimension: s.dimension,
        time_of_day: s.timeOfDay,
        health: s.health !== undefined ? _round(s.health) : null,
        food: s.food,
        main_hand: _itemLabel(s.equipment?.main_hand),
        armor,
        inventory,
        slots: s.inventory_slots ? `${s.inventory_slots.used}/${s.inventory_slots.total}` : null,
        nearby: s.nearby,
        entities: nearbyEntities,
        home: s.home ?? null,
        chests: (s.chests ?? []).map(c => ({ id: c.id, label: c.label ?? null, free_slots: c.freeSlots ?? null })),
    }
    if (s.mc_connected === false) result.mc_connected = false
    const wait = s.activity_runtime?.wait
    if (wait) result.waiting_for = wait
    return result
}

function _trimValue(value) {
    if (Array.isArray(value)) return value.length > 8 ? [`…${value.length - 8} earlier`, ...value.slice(-8)] : value
    return value
}

function compactEvent(e) {
    const result = { seq: e.seq, type: e.type, at: e.at }
    if (e.command_id) result.command_id = e.command_id
    if (e.activity && e.type !== 'tick') result.activity = e.activity
    for (const [key, value] of Object.entries(e)) {
        if (BASE_KEYS.has(key) || key === 'seq' || key === 'at') continue
        result[key] = _trimValue(value)
    }
    if (FAILED.has(e.type) && e.navigation) result.navigation = e.navigation
    return result
}

// ── Daemon ───────────────────────────────────────────────────

function freePort() {
    return new Promise((resolve, reject) => {
        const server = net.createServer()
        server.once('error', reject)
        server.listen(0, '127.0.0.1', () => {
            const { port } = server.address()
            server.close(() => resolve(port))
        })
    })
}

async function runDaemon(name) {
    const dir = stateDir(name)
    const config = readJson(path.join(dir, 'config.json'))
    if (!config) throw new Error(`missing ${path.join(dir, 'config.json')}`)
    const WebSocket = createRequire(path.join(BOT_DIR, 'package.json'))('ws')
    const dataDir = path.join(dir, 'data')
    const logDir = path.join(dir, 'logs')
    fs.mkdirSync(dataDir, { recursive: true })
    fs.mkdirSync(logDir, { recursive: true })

    const wsPort = await freePort()
    const botLog = fs.createWriteStream(path.join(dir, 'bot.log'), { flags: 'w' })
    const child = spawn(process.execPath, [path.join(BOT_DIR, 'index.js')], {
        cwd: BOT_DIR,
        env: {
            ...process.env,
            MC_HOST: config.host,
            MC_PORT: String(config.port),
            MC_USERNAME: config.username,
            MC_AUTH: config.auth,
            MC_VERSION: config.version,
            MC_FORGE: config.forge ? 'true' : 'false',
            STRICT_CHAT_ADDRESSING: config.chat === 'all' ? 'false' : 'true',
            BOT_WS_PORT: String(wsPort),
            BOT_WS_HOST: '127.0.0.1',
            BOT_DATA_DIR: dataDir,
            BOT_LOG_DIR: logDir,
        },
        stdio: ['ignore', 'pipe', 'pipe'],
    })

    let latest = null
    let latestAt = 0
    let wsConnected = false
    let botExit = null
    let authPrompt = null
    let seq = 0
    let delivered = 0
    const events = []
    const waiters = new Set()

    function pushEvent(event) {
        const stamped = { ...event, seq: ++seq, at: new Date().toISOString().slice(11, 19) }
        events.push(stamped)
        if (events.length > MAX_EVENTS) events.shift()
        for (const waiter of [...waiters]) waiter.check()
    }

    const onOutput = (chunk) => {
        botLog.write(chunk)
        const text = chunk.toString()
        const match = text.match(/[^\n]*microsoft\.com\/link[^\n]*/i)
        if (match) authPrompt = match[0].trim()
    }
    child.stdout.on('data', onOutput)
    child.stderr.on('data', onOutput)
    child.on('exit', (code, signal) => {
        botExit = { code, signal }
        wsConnected = false
        pushEvent({ type: 'bot_exited', code, signal })
    })

    // The bot only registers its socket handler after spawning into the world, and
    // keeps the most recent connection. Reconnect whenever the socket goes silent.
    let socket = null
    let lastMessageAt = 0
    function connect() {
        if (botExit) return
        socket = new WebSocket(`ws://127.0.0.1:${wsPort}`)
        socket.on('open', () => { lastMessageAt = Date.now() })
        socket.on('message', (raw) => {
            lastMessageAt = Date.now()
            wsConnected = true
            let msg
            try { msg = JSON.parse(raw) } catch { return }
            if (latest?.mc_connected && msg.mc_connected === false) pushEvent({ type: 'mc_disconnected' })
            if (msg.pos !== undefined) { latest = msg; latestAt = Date.now() }
            if (msg.type !== 'tick') pushEvent(msg)
        })
        socket.on('error', () => {})
        socket.on('close', () => {
            wsConnected = false
            socket = null
            if (!botExit) setTimeout(connect, 1000)
        })
    }
    setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN && Date.now() - lastMessageAt > 10000) socket.terminate()
    }, 3000)
    setTimeout(connect, 1000)

    // Events are sent before the activity frame is popped, so the snapshot taken with
    // them is stale; wait for the next state message (ticks arrive every 2 s).
    async function freshState() {
        const since = Date.now()
        for (let i = 0; i < 25 && latestAt <= since && !botExit; i++) await new Promise(r => setTimeout(r, 100))
        return compactState(latest)
    }

    function takeUndelivered() {
        const fresh = events.filter(e => e.seq > delivered)
        if (fresh.length) delivered = fresh[fresh.length - 1].seq
        return fresh.map(compactEvent)
    }

    // Resolves when predicate(undeliveredEvents) is true or the timeout expires.
    function waitUntil(predicate, timeoutMs) {
        return new Promise((resolve) => {
            const waiter = {
                check() {
                    if (!predicate(events.filter(e => e.seq > delivered))) return
                    finish(false)
                },
            }
            const timer = setTimeout(() => finish(true), timeoutMs)
            function finish(timedOut) {
                clearTimeout(timer)
                waiters.delete(waiter)
                resolve(timedOut)
            }
            waiters.add(waiter)
            waiter.check()
        })
    }

    function sendMessage(msg) {
        if (!socket || socket.readyState !== WebSocket.OPEN || !wsConnected) throw new Error('bot is not connected')
        socket.send(JSON.stringify(msg))
    }

    let commandCounter = 0
    async function runCommand(line, timeoutMs) {
        const parts = String(line).trim().split(/\s+/).filter(Boolean)
        if (!parts.length) throw new Error('empty command')
        const [command, ...args] = parts
        if (command === 'idle') return { status: 'done', note: 'idle is a no-op' }
        if (!ACTIVITY.has(command) && !ACTION_DONE.has(command) && !NO_ACK.has(command)) {
            throw new Error(`unknown command "${command}" — see references/commands.md`)
        }
        const commandId = `mcctl-${process.pid}-${++commandCounter}`
        sendMessage({ command, args, text: args.join(' '), _command_id: commandId })
        if (NO_ACK.has(command)) {
            await new Promise(r => setTimeout(r, 1500))
            return { command_id: commandId, status: 'sent' }
        }
        const mine = e => e.command_id === commandId
        const timedOut = await waitUntil(fresh => fresh.some(e => (mine(e) && TERMINAL.has(e.type)) || INTERRUPT.has(e.type)), timeoutMs)
        const fresh = events.filter(e => e.seq > delivered)
        const terminal = fresh.find(e => mine(e) && TERMINAL.has(e.type))
        let status
        if (terminal) status = FAILED.has(terminal.type) ? 'stuck' : 'done'
        else if (!timedOut) status = 'interrupted'
        else if (fresh.some(e => mine(e) && e.type === 'task_started')) status = 'running'
        else status = ACTIVITY.has(command) ? 'not_started' : 'timeout'
        return { command_id: commandId, status }
    }

    function health() {
        return {
            name,
            bot_running: !botExit,
            bot_exit: botExit,
            ws_connected: wsConnected,
            mc_connected: latest?.mc_connected ?? false,
            auth_prompt: authPrompt,
            server: `${config.host}:${config.port}`,
            username: config.username,
            undelivered_events: events.filter(e => e.seq > delivered && !ROUTINE.has(e.type)).length,
        }
    }

    function shutdown() {
        try { fs.unlinkSync(path.join(dir, 'daemon.json')) } catch {}
        if (!botExit) {
            child.kill('SIGTERM')
            setTimeout(() => { if (!botExit) child.kill('SIGKILL') }, 3000).unref()
        }
        child.once('exit', () => process.exit(0))
        if (botExit) process.exit(0)
    }
    process.on('SIGTERM', shutdown)
    process.on('SIGINT', shutdown)

    async function route(req, url, body) {
        switch (`${req.method} ${url.pathname}`) {
            case 'GET /health': return health()
            case 'GET /state':
                return url.searchParams.get('full') ? latest : compactState(latest)
            case 'GET /events': {
                const last = Math.min(MAX_EVENTS, Number(url.searchParams.get('last') || 20))
                return { events: events.slice(-last).map(compactEvent) }
            }
            case 'GET /wait': {
                const timeoutMs = Math.min(600, Number(url.searchParams.get('timeout') || 90)) * 1000
                const timedOut = await waitUntil(fresh => fresh.some(e => !ROUTINE.has(e.type)), timeoutMs)
                return { timeout: timedOut, events: takeUndelivered(), state: timedOut ? compactState(latest) : await freshState() }
            }
            case 'POST /run': {
                const timeoutMs = Math.min(600, Number(body.timeout || 60)) * 1000
                const result = await runCommand(body.line, timeoutMs)
                const state = result.status === 'running' ? compactState(latest) : await freshState()
                return { ...result, events: takeUndelivered(), state }
            }
            case 'POST /send':
                sendMessage(body.message)
                return { sent: true }
            case 'POST /shutdown':
                setTimeout(shutdown, 100)
                return { stopping: true }
            default:
                return null
        }
    }

    const server = http.createServer(async (req, res) => {
        let raw = ''
        req.on('data', c => { raw += c })
        await new Promise(r => req.on('end', r))
        const url = new URL(req.url, 'http://127.0.0.1')
        try {
            const result = await route(req, url, raw ? JSON.parse(raw) : {})
            if (result === null && url.pathname !== '/state') {
                res.writeHead(404).end(JSON.stringify({ error: 'not found' }))
                return
            }
            res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(result))
        } catch (err) {
            res.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ error: err.message }))
        }
    })
    server.listen(0, '127.0.0.1', () => {
        fs.writeFileSync(path.join(dir, 'daemon.json'), JSON.stringify({
            pid: process.pid, bot_pid: child.pid, ctl_port: server.address().port, ws_port: wsPort,
            started_at: new Date().toISOString(),
        }))
    })
}

// ── Client ───────────────────────────────────────────────────

async function call(name, method, pathname, body, timeoutMs = 15000) {
    const info = readJson(path.join(stateDir(name), 'daemon.json'))
    if (!info || !isAlive(info.pid)) throw new Error(`bot "${name}" is not running — use: mcctl start`)
    const res = await fetch(`http://127.0.0.1:${info.ctl_port}${pathname}`, {
        method,
        headers: body ? { 'content-type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(timeoutMs),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data?.error ?? `HTTP ${res.status}`)
    return data
}

function tail(file, lines) {
    try { return fs.readFileSync(file, 'utf8').trimEnd().split('\n').slice(-lines) } catch { return [] }
}

async function cmdStart(name, flags) {
    const [major] = process.versions.node.split('.').map(Number)
    if (major < 22) throw new Error(`Node.js 22+ is required (found ${process.versions.node})`)
    if (!fs.existsSync(path.join(BOT_DIR, 'node_modules', 'mineflayer'))) {
        throw new Error('dependencies are not installed — run: mcctl setup')
    }
    const dir = stateDir(name)
    const existing = readJson(path.join(dir, 'daemon.json'))
    if (existing && isAlive(existing.pid)) {
        const status = await call(name, 'GET', '/health')
        if (status.bot_running) return out({ already_running: true, ...status })
        await call(name, 'POST', '/shutdown').catch(() => {})
        await new Promise(r => setTimeout(r, 1500))
    }

    const config = {
        host: flags.host || process.env.MC_HOST || 'localhost',
        port: Number(flags.port || process.env.MC_PORT || 25565),
        username: flags.username || process.env.MC_USERNAME || 'Agent',
        auth: flags.auth || process.env.MC_AUTH || 'offline',
        version: flags.version || process.env.MC_VERSION || 'auto',
        forge: flags.forge === true || flags.forge === 'true',
        chat: flags.chat || 'addressed',
    }
    if (!['offline', 'microsoft'].includes(config.auth)) throw new Error('--auth must be offline or microsoft')
    if (!['addressed', 'all'].includes(config.chat)) throw new Error('--chat must be addressed or all')
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, 'config.json'), JSON.stringify(config, null, 2))
    try { fs.unlinkSync(path.join(dir, 'daemon.json')) } catch {}

    const daemonLog = fs.openSync(path.join(dir, 'daemon.log'), 'w')
    const daemon = spawn(process.execPath, [__filename, '__daemon', '--name', name], {
        detached: true, stdio: ['ignore', daemonLog, daemonLog],
    })
    daemon.unref()

    const deadline = Date.now() + Number(flags.timeout || 90) * 1000
    let status = null
    let shownPrompt = null
    while (Date.now() < deadline) {
        await new Promise(r => setTimeout(r, 1000))
        if (!isAlive(daemon.pid)) break
        status = await call(name, 'GET', '/health').catch(() => null)
        if (!status) continue
        if (status.auth_prompt && status.auth_prompt !== shownPrompt) {
            shownPrompt = status.auth_prompt
            process.stderr.write(`Microsoft sign-in required: ${status.auth_prompt}\n`)
        }
        if (!status.bot_running) break
        if (status.ws_connected && status.mc_connected) {
            const state = await call(name, 'GET', '/state')
            return out({ started: true, ...status, state })
        }
    }
    out({
        started: false,
        status,
        hint: 'check host/port/version, that the server is reachable, and online-mode/auth settings',
        bot_log: tail(path.join(dir, 'bot.log'), 25),
        daemon_log: tail(path.join(dir, 'daemon.log'), 10),
    }, 1)
}

async function cmdStop(name) {
    const dir = stateDir(name)
    const info = readJson(path.join(dir, 'daemon.json'))
    if (!info) return out({ stopped: true, note: 'not running' })
    await call(name, 'POST', '/shutdown').catch(() => {})
    for (let i = 0; i < 20 && (isAlive(info.pid) || isAlive(info.bot_pid)); i++) {
        await new Promise(r => setTimeout(r, 250))
    }
    for (const pid of [info.bot_pid, info.pid]) if (isAlive(pid)) process.kill(pid, 'SIGKILL')
    try { fs.unlinkSync(path.join(dir, 'daemon.json')) } catch {}
    out({ stopped: true })
}

function cmdSetup() {
    const hasLock = fs.existsSync(path.join(BOT_DIR, 'package-lock.json'))
    const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
    const result = spawnSync(npm, [hasLock ? 'ci' : 'install', '--omit=dev', '--no-audit', '--no-fund'], {
        cwd: BOT_DIR, stdio: 'inherit', shell: process.platform === 'win32',
    })
    if (result.status !== 0) return out({ installed: false, exit_code: result.status }, 1)
    out({ installed: true, bot_dir: BOT_DIR })
}

const USAGE = `usage: mcctl <command> [--name <bot>]
  setup                                   install bot dependencies (npm ci)
  start [--host H] [--port P] [--username U] [--version 1.20.1|auto]
        [--auth offline|microsoft] [--forge] [--chat addressed|all]
  status                                  daemon / connection health
  state [--full]                          compact (or raw) bot state
  run <command words...> [--timeout S]    send one bot command, block until it finishes,
                                          needs attention, or S seconds pass (default 60)
  wait [--timeout S]                      block until the next event (default 90)
  send '<json>'                           send a raw message, e.g. a *_decision reply
  events [--last N]                       re-read recent events (does not consume them)
  log [--lines N]                         tail the bot's console log
  stop                                    stop the bot and daemon`

async function main() {
    const [subcommand, ...rest] = process.argv.slice(2)
    const { flags, positional } = parseArgs(rest)
    const name = flags.name || process.env.MCBOT_NAME || 'default'
    const timeout = Number(flags.timeout || 0)
    switch (subcommand) {
        case '__daemon': return runDaemon(name)
        case 'setup': return cmdSetup()
        case 'start': return cmdStart(name, flags)
        case 'stop': return cmdStop(name)
        case 'status': return out(await call(name, 'GET', '/health'))
        case 'state': return out(await call(name, 'GET', flags.full ? '/state?full=1' : '/state'))
        case 'events': return out(await call(name, 'GET', `/events?last=${Number(flags.last || 20)}`))
        case 'log': return out({ lines: tail(path.join(stateDir(name), 'bot.log'), Number(flags.lines || 40)) })
        case 'wait': {
            const seconds = timeout || 90
            return out(await call(name, 'GET', `/wait?timeout=${seconds}`, null, (seconds + 10) * 1000))
        }
        case 'run': {
            if (!positional.length) throw new Error('usage: mcctl run <command words...>')
            const seconds = timeout || 60
            return out(await call(name, 'POST', '/run', { line: positional.join(' '), timeout: seconds }, (seconds + 10) * 1000))
        }
        case 'send': {
            if (!positional.length) throw new Error(`usage: mcctl send '{"command":"craft_decision","item":"oak_planks"}'`)
            const message = JSON.parse(positional.join(' '))
            if (!message || typeof message.command !== 'string') throw new Error('message needs a "command" field')
            return out(await call(name, 'POST', '/send', { message }))
        }
        default:
            process.stderr.write(USAGE + '\n')
            process.exitCode = subcommand ? 2 : 0
    }
}

main().catch((err) => out({ error: err.message }, 1))
