const fs = require('fs')
const path = require('path')

let _currentTaskId = null
let _botId = ''

function setTaskId(id) { _currentTaskId = id || null }

function _sanitizeLabel(value) {
    return String(value || '')
        .trim()
        .replace(/[^A-Za-z0-9_-]+/g, '-')
        .replace(/^[-_]+|[-_]+$/g, '') || 'bot'
}

function _resolveLogLabel() {
    const botId = String(process.env.BOT_ID || '').trim()
    if (botId) return _sanitizeLabel(botId)

    const dataDir = String(process.env.BOT_DATA_DIR || '').trim()
    if (dataDir) {
        const base = path.basename(path.normalize(dataDir))
        if (/^bot\d+$/i.test(base)) return _sanitizeLabel(base)
    }

    const mcUsername = String(process.env.MC_USERNAME || '').trim()
    if (/^Agent\d+$/i.test(mcUsername)) return _sanitizeLabel(mcUsername)
    return ''
}

function _filenameStamp() {
    const now = new Date()
    const pad = (n) => String(n).padStart(2, '0')
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

function _formatArg(arg) {
    if (typeof arg === 'string') return arg
    if (arg instanceof Error) return arg.stack || arg.message
    try {
        return JSON.stringify(arg)
    } catch (_) {
        return String(arg)
    }
}

function _cleanupOldLogs(logDir, keepDays = 7) {
    const cutoffMs = Date.now() - keepDays * 86400 * 1000
    try {
        for (const fname of fs.readdirSync(logDir)) {
            const fpath = path.join(logDir, fname)
            try {
                if (fs.statSync(fpath).mtimeMs < cutoffMs) fs.unlinkSync(fpath)
            } catch (_) {}
        }
    } catch (_) {}
}

function initLogger(name = 'bot') {
    if (global.__agentLoggerInitialized) return global.__agentLoggerPath

    const logDir = path.join(__dirname, '..', 'logs')
    fs.mkdirSync(logDir, { recursive: true })
    _cleanupOldLogs(logDir)
    const botLabel = _resolveLogLabel()
    _botId = botLabel || 'bot'
    const filename = botLabel ? `${name}-${botLabel}-${_filenameStamp()}.jsonl` : `${name}-${_filenameStamp()}.jsonl`
    const logPath = path.join(logDir, filename)

    const original = {
        log: console.log.bind(console),
        warn: console.warn.bind(console),
        error: console.error.bind(console),
    }

    const write = (level, args) => {
        const entry = {
            time: new Date().toISOString(),
            level,
            service: name,
            bot_id: _botId,
            task_id: _currentTaskId,
            msg: args.map(_formatArg).join(' '),
        }
        fs.appendFileSync(logPath, JSON.stringify(entry) + '\n', 'utf8')
    }

    console.log = (...args) => {
        write('INFO', args)
        original.log(...args)
    }
    console.warn = (...args) => {
        write('WARN', args)
        original.warn(...args)
    }
    console.error = (...args) => {
        write('ERROR', args)
        original.error(...args)
    }

    global.__agentLoggerInitialized = true
    global.__agentLoggerPath = logPath
    console.log(`[Log] 已寫入 ${logPath}`)
    return logPath
}

module.exports = { initLogger, setTaskId }
