const path = require('path')
const fs = require('fs')

const FILE = path.join(process.env.BOT_DATA_DIR || path.join(__dirname, '../data'), 'mode.json')
const VALID = ['companion', 'survival', 'workflow']

let _mode = 'survival'
try { _mode = JSON.parse(fs.readFileSync(FILE, 'utf8')).mode || 'survival' } catch (_) {}

function getMode() { return _mode }

function setMode(m) {
    if (!VALID.includes(m)) return false
    _mode = m
    try { fs.writeFileSync(FILE, JSON.stringify({ mode: m }, null, 2)) } catch (_) {}
    return true
}

module.exports = { getMode, setMode }
