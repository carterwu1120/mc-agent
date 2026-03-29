const fs = require('fs')
const path = require('path')

function _timestamp() {
    const now = new Date()
    const pad = (n) => String(n).padStart(2, '0')
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
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

function initLogger(name = 'bot') {
    if (global.__agentLoggerInitialized) return global.__agentLoggerPath

    const logDir = path.join(__dirname, '..', 'logs')
    fs.mkdirSync(logDir, { recursive: true })
    const logPath = path.join(logDir, `${name}-${_filenameStamp()}.txt`)

    const original = {
        log: console.log.bind(console),
        warn: console.warn.bind(console),
        error: console.error.bind(console),
    }

    const write = (level, args) => {
        const line = `[${_timestamp()}] [${level}] ${args.map(_formatArg).join(' ')}\n`
        fs.appendFileSync(logPath, line, 'utf8')
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

module.exports = { initLogger }
