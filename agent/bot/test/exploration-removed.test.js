const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')

for (const command of ['explore', 'stopexplore']) {
    test(`legacy ${command} reports failure without moving or succeeding`, () => {
        const events = []
        const module = { exports: {} }
        const execution = { isCurrent: () => true, run: fn => fn() }
        const unexpected = () => { throw new Error('unexpected game action') }
        vm.runInNewContext(fs.readFileSync(require.resolve('../commands'), 'utf8'), {
            module, exports: module.exports, console: { log() {}, warn() {} },
            require: name => {
                if (name === './explore') throw new Error('removed exploration loaded')
                if (name === './execution') return execution
                if (name === './bridge') return { sendState: (bot, type, data) => events.push({ type, ...data }) }
                return new Proxy({}, { get: () => unexpected })
            },
        })
        module.exports.handle({}, { command, args: ['animals'], _command_id: 'old-plan' })
        assert.equal(events.length, 1)
        assert.equal(events[0].type, 'activity_stuck')
        assert.equal(events[0].reason, 'unsupported_command')
    })
}
