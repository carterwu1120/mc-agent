// Decisions consume measured inventory changes, never blocks merely broken.
function itemName(target) {
    return { stone: 'cobblestone', iron: 'raw_iron', gold: 'raw_gold',
        copper: 'raw_copper', lapis: 'lapis_lazuli' }[target] || target
}
function count(bot, target) {
    return bot.inventory.items().filter(i => i.name === itemName(target))
        .reduce((n, i) => n + i.count, 0)
}
function matches(name, target) {
    if (!target) return name.endsWith('_ore')
    const ore = target.replace(/^raw_/, '').replace(/^deepslate_/, '').replace(/_ore$/, '')
    return name === `${ore}_ore` || name === `deepslate_${ore}_ore`
}
function snapshot(bot, target) {
    return { count: count(bot, target), pos: { ...bot.entity.position } }
}
function result(bot, goal, before, phase, reason) {
    return { status: reason === 'segment_complete' ? 'yielded' : 'blocked', phase, reason,
        target: goal.target, requested: goal.count,
        changes: { target_gained: Math.max(0, count(bot, goal.target) - before.count),
            position: { before: before.pos, after: { ...bot.entity.position } } } }
}
module.exports = { count, matches, snapshot, result }
