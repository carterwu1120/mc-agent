// Fence commands which outlive a recovery/stop. Background safety monitors run
// outside these scopes and remain available while the planner is thinking.
const { AsyncLocalStorage } = require('node:async_hooks')
const storage = new AsyncLocalStorage()
let epoch = 0

function run(fn, commandId = null) { return storage.run({ epoch, commandId, active: true }, fn) }
function commandId() { return storage.getStore()?.commandId ?? null }
function current() { return storage.getStore() }
function cancelScope(scope) { if (scope) scope.active = false }
function isCurrent() { return !storage.getStore() || (storage.getStore().active && storage.getStore().epoch === epoch) }
function invalidate() { epoch++ }
function owned(fn) { return (...args) => isCurrent() ? run(() => fn(...args), commandId()) : undefined }
function cancelled() { return Object.assign(new Error('Action superseded'), { name: 'AbortError' }) }

function install(bot) {
    function wrap(object, names, asyncResult) {
        for (const name of names) {
            if (typeof object?.[name] !== 'function') continue
            const original = object[name].bind(object)
            object[name] = function (...args) {
                if (!asyncResult) return isCurrent() ? original(...args) : undefined
                const result = isCurrent()
                    ? Promise.resolve().then(() => {
                        if (!isCurrent()) throw cancelled()
                        return original(...args)
                    }).then(value => {
                        if (!isCurrent()) throw cancelled()
                        if (name === 'openChest' || name === 'openFurnace') {
                            wrap(value, ['deposit', 'withdraw', 'putFuel', 'putInput', 'takeOutput', 'takeInput', 'takeFuel'], true)
                            wrap(value, ['close'], false)
                        }
                        return value
                    })
                    : Promise.reject(cancelled())
                // Some legacy callers intentionally do not await their operation.
                result.catch(() => {})
                return result
            }
        }
    }
    wrap(bot, ['dig', 'placeBlock', 'equip', 'unequip', 'craft', 'toss', 'tossStack', 'consume', 'fish', 'lookAt', 'look', 'openChest', 'openFurnace'], true)
    wrap(bot, ['setControlState', 'clearControlStates', 'stopDigging', 'activateItem', 'deactivateItem', 'attack', 'chat', 'closeWindow'], false)
    wrap(bot.pathfinder, ['goto'], true)
    wrap(bot.pathfinder, ['setGoal', 'setMovements', 'stop'], false)
}

module.exports = { run, isCurrent, invalidate, install, commandId, current, cancelScope, owned }
