const test = require('node:test')
const assert = require('node:assert/strict')
const { mkdtempSync, writeFileSync, rmSync, rmdirSync } = require('node:fs')
const { tmpdir } = require('node:os')
const { join } = require('node:path')
const { loadProjectEnv, connectionOptions } = require('../config')

test('a LAN port is preserved and invalid ports fail clearly', () => {
    assert.equal(connectionOptions({ MC_PORT: '51234' }).port, 51234)
    for (const port of ['abc', '0', '65536', '25565oops']) {
        assert.throws(() => connectionOptions({ MC_PORT: port }), /MC_PORT/)
    }
    assert.equal(connectionOptions({ MC_AUTH: 'microsoft', MC_VERSION: 'auto' }).version, false)
})

test('loading project env does not replace explicitly supplied environment', () => {
    const dir = mkdtempSync(join(tmpdir(), 'mc-agent-env-'))
    const file = join(dir, '.env')
    const before = process.env.MC_AGENT_ENV_TEST
    try {
        writeFileSync(file, 'MC_AGENT_ENV_TEST=file\n')
        process.env.MC_AGENT_ENV_TEST = 'shell'
        loadProjectEnv(file)
        assert.equal(process.env.MC_AGENT_ENV_TEST, 'shell')
    } finally {
        if (before === undefined) delete process.env.MC_AGENT_ENV_TEST
        else process.env.MC_AGENT_ENV_TEST = before
        rmSync(file); rmdirSync(dir)
    }
})
