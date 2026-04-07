const fs = require('fs')
const path = require('path')
const { goals, Movements } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const { convertLogsToPlanks, ensureCraftingTable, reclaimCraftingTable } = require('./crafting')

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

const DATA_FILE = path.join(__dirname, '..', 'data', 'chests.json')

const LABEL_PATTERNS = {
    food:  ['cooked_', 'bread', 'apple', 'carrot', 'potato', 'raw_beef', 'raw_pork', 'raw_chicken', 'raw_mutton', 'raw_rabbit', 'raw_salmon', 'raw_cod'],
    wood:  ['_log', '_planks', '_sapling', 'bamboo'],
    stone: ['cobblestone', 'deepslate', 'gravel', 'sand', 'diorite', 'andesite', 'granite', 'tuff', 'calcite'],
    ore:   ['_ingot', 'raw_iron', 'raw_gold', 'raw_copper', 'lapis_lazuli', 'quartz'],
}

// ore items that must be exact name matches (avoid matching tool/armor names)
const ORE_EXACT = new Set(['diamond', 'emerald', 'coal', 'netherite_scrap', 'netherite_ingot', 'ancient_debris', 'amethyst_shard'])

// never deposit these regardless of label
const EQUIPMENT_SUFFIXES = ['_pickaxe', '_axe', '_sword', '_shovel', '_hoe', '_helmet', '_chestplate', '_leggings', '_boots', '_bow', 'crossbow', 'shield', 'elytra', 'trident']

function _isEquipment(name) {
    return EQUIPMENT_SUFFIXES.some(s => name.endsWith(s)) || name === 'crafting_table' || name === 'furnace'
}

function _itemLabel(name) {
    if (_isEquipment(name)) return null  // never deposit equipment
    if (ORE_EXACT.has(name)) return 'ore'
    for (const [label, patterns] of Object.entries(LABEL_PATTERNS)) {
        if (patterns.some(p => name.includes(p))) return label
    }
    return 'misc'
}

function _load() {
    try { return JSON.parse(fs.readFileSync(DATA_FILE, 'utf8')) } catch (_) { return [] }
}

function _save(data) {
    fs.mkdirSync(path.dirname(DATA_FILE), { recursive: true })
    fs.writeFileSync(DATA_FILE, JSON.stringify(data, null, 2))
}

function _isContainer(name) {
    return name === 'chest' || name === 'barrel' || name === 'trapped_chest'
}

async function _gotoChest(bot, pos) {
    const movements = new Movements(bot)
    movements.canDig = false
    bot.pathfinder.setMovements(movements)
    try {
        await bot.pathfinder.goto(new goals.GoalNear(pos.x, pos.y, pos.z, 2))
    } catch (_) {}
    bot.pathfinder.setMovements(new Movements(bot))
}

// !setchest — register nearest chest/barrel block
function setChest(bot) {
    const block = bot.findBlock({ matching: b => _isContainer(b.name), maxDistance: 4 })
    if (!block) { bot.chat('附近沒有找到箱子'); return }
    const chests = _load()
    const exists = chests.find(c => c.pos.x === block.position.x && c.pos.y === block.position.y && c.pos.z === block.position.z)
    if (exists) { bot.chat(`箱子已登記 (id ${exists.id}，label: ${exists.label ?? '未分類'})`); return }
    const id = chests.length ? Math.max(...chests.map(c => c.id)) + 1 : 1
    chests.push({ id, pos: { x: block.position.x, y: block.position.y, z: block.position.z }, label: null, contents: [], updatedAt: Date.now() })
    _save(chests)
    console.log(`[Chest] 登記箱子 id=${id} @ (${block.position.x}, ${block.position.y}, ${block.position.z})`)
    bot.chat(`箱子已登記 id=${id}，尚未分類`)
}

// labelchest <id> <label> — LLM assigns label
function labelChest(id, label) {
    const chests = _load()
    const c = chests.find(c => c.id === id)
    if (!c) { console.log(`[Chest] 找不到箱子 id=${id}`); return false }
    c.label = label
    _save(chests)
    console.log(`[Chest] 箱子 id=${id} 標記為 "${label}"`)
    return true
}

// readchest [id] — open chest, cache contents, close
async function readChest(bot, id) {
    const chests = _load()
    const target = id ? chests.find(c => c.id === id) : chests.find(Boolean)
    if (!target) { bot.chat('找不到指定箱子'); return }
    await _gotoChest(bot, target.pos)
    const block = bot.blockAt(new Vec3(target.pos.x, target.pos.y, target.pos.z))
    if (!block || !_isContainer(block.name)) { bot.chat(`箱子 id=${target.id} 位置無法存取`); return }
    try {
        const container = await bot.openContainer(block)
        target.contents = container.containerItems().map(i => ({ name: i.name, count: i.count }))
        target.totalSlots = container.slots.length - 36  // subtract player inv(27) + hotbar(9)
        target.usedSlots = target.contents.length
        target.freeSlots = target.totalSlots - target.usedSlots
        target.updatedAt = Date.now()
        container.close()
        _save(chests)
        console.log(`[Chest] 讀取箱子 id=${target.id}，${target.contents.length} 種物品`)
    } catch (e) { console.log('[Chest] readChest 失敗:', e.message) }
}

// deposit <chest_id> — put inventory items matching chest label into chest
async function depositToChest(bot, chestId) {
    const chests = _load()
    const target = chests.find(c => c.id === chestId)
    if (!target) { bot.chat(`找不到箱子 id=${chestId}`); return }
    if (!target.label) { bot.chat(`箱子 id=${chestId} 尚未分類，請先 labelchest`); return }
    await _gotoChest(bot, target.pos)
    const block = bot.blockAt(new Vec3(target.pos.x, target.pos.y, target.pos.z))
    if (!block || !_isContainer(block.name)) { bot.chat(`箱子 id=${chestId} 位置無效`); return }
    try {
        const container = await bot.openContainer(block)
        const toDeposit = bot.inventory.items().filter(i => !_isEquipment(i.name) && _itemLabel(i.name) === target.label)
        let deposited = 0
        for (const item of toDeposit) {
            try { await container.deposit(item.type, null, item.count); deposited++ } catch (_) {}
        }
        target.contents = container.containerItems().map(i => ({ name: i.name, count: i.count }))
        target.totalSlots = container.slots.length - 36  // subtract player inv(27) + hotbar(9)
        target.usedSlots = target.contents.length
        target.freeSlots = target.totalSlots - target.usedSlots
        target.updatedAt = Date.now()
        container.close()
        _save(chests)
        console.log(`[Chest] 存入 ${deposited} 種物品到箱子 id=${chestId} (${target.label})`)
    } catch (e) { console.log('[Chest] depositToChest 失敗:', e.message) }
}

// withdraw <item> [count] <chest_id> — take items from specified chest
async function withdrawFromChest(bot, itemName, count, chestId) {
    const chests = _load()
    const target = chests.find(c => c.id === chestId)
    if (!target) { bot.chat(`找不到箱子 id=${chestId}`); return }
    await _gotoChest(bot, target.pos)
    const block = bot.blockAt(new Vec3(target.pos.x, target.pos.y, target.pos.z))
    if (!block || !_isContainer(block.name)) { bot.chat(`箱子 id=${chestId} 位置無效`); return }
    try {
        const container = await bot.openContainer(block)
        const item = container.containerItems().find(i => i.name === itemName)
        if (!item) { bot.chat(`箱子 id=${chestId} 沒有 ${itemName}`); container.close(); return }
        const amt = count ? Math.min(count, item.count) : item.count
        await container.withdraw(item.type, null, amt)
        target.contents = container.containerItems().map(i => ({ name: i.name, count: i.count }))
        target.totalSlots = container.slots.length - 36  // subtract player inv(27) + hotbar(9)
        target.usedSlots = target.contents.length
        target.freeSlots = target.totalSlots - target.usedSlots
        target.updatedAt = Date.now()
        container.close()
        _save(chests)
        console.log(`[Chest] 從箱子 id=${chestId} 取出 ${amt}x ${itemName}`)
    } catch (e) { console.log('[Chest] withdrawFromChest 失敗:', e.message) }
}

function _isAir(b) { return b && (b.name === 'air' || b.name === 'cave_air') }

function _findGround(bot, dx, dz) {
    const pos = bot.entity.position.floored()
    for (let dy = -1; dy >= -3; dy--) {
        const b = bot.blockAt(pos.offset(dx, dy, dz))
        if (b && b.boundingBox === 'block' && !_isContainer(b.name)) return b
    }
    return null
}

function _checkSpot(bot, ground) {
    const chestSpace = bot.blockAt(ground.position.offset(0, 1, 0))
    const lidSpace   = bot.blockAt(ground.position.offset(0, 2, 0))
    return _isAir(chestSpace) && _isAir(lidSpace)
}

// First chest: directly in front of agent
// Second chest: to the left OR right of the first (side by side, both facing agent → merge)
function _findLargeChestSpot(bot) {
    // Snap to nearest cardinal direction (N/S/E/W) to avoid diagonal placement
    const snapped = Math.round(bot.entity.yaw / (Math.PI / 2)) * (Math.PI / 2)
    const fdx = Math.round(-Math.sin(snapped))
    const fdz = Math.round(-Math.cos(snapped))
    // Right of forward: rotate 90° clockwise
    const rdx = -fdz
    const rdz = fdx

    // Try 2 and 3 blocks in front (need space between bot and chest)
    for (const dist of [2, 3]) {
        const g1 = _findGround(bot, fdx * dist, fdz * dist)
        if (!g1 || !_checkSpot(bot, g1)) continue

        // Try right side first, then left
        for (const sign of [1, -1]) {
            const g2 = _findGround(bot, fdx * dist + sign * rdx, fdz * dist + sign * rdz)
            if (g2 && _checkSpot(bot, g2)) return [g1, g2]
        }
    }
    return null
}

async function _placeChest(bot) {
    const spot = _findLargeChestSpot(bot)
    if (!spot) { bot.chat('找不到放置大箱子的位置（需要兩格相鄰空地）'); return false }

    const placed = []
    for (const ground of spot) {
        const item = bot.inventory.items().find(i => i.name === 'chest')
        if (!item) { bot.chat('箱子不夠'); return false }
        try {
            await bot.equip(item, 'hand')
            // Look at chest-level of target position so facing is consistent (toward bot)
            await bot.lookAt(ground.position.offset(0.5, 1, 0.5))
            await bot.placeBlock(ground, new Vec3(0, 1, 0))
            await _sleep(400)
            const block = bot.blockAt(ground.position.offset(0, 1, 0))
            if (block && _isContainer(block.name)) {
                placed.push(block.position)
            }
        } catch (e) {
            console.log('[Chest] 放置箱子失敗:', e.message)
            return false
        }
    }

    if (placed.length === 0) { bot.chat('放置失敗'); return false }

    // Register as one entry (use first block position as canonical)
    const chests = _load()
    const id = chests.length ? Math.max(...chests.map(c => c.id)) + 1 : 1
    const p = placed[0]
    chests.push({ id, pos: { x: p.x, y: p.y, z: p.z }, label: null, contents: [], totalSlots: 54, usedSlots: 0, freeSlots: 54, updatedAt: Date.now() })
    _save(chests)
    console.log(`[Chest] 放置大箱子 id=${id} @ (${p.x}, ${p.y}, ${p.z})`)
    bot.chat(`大箱子已放置並登記 id=${id}，等待分類`)
    return id
}

// craftAndPlaceChest — 合成 2 個箱子（若不夠）並放置大箱子，自動登記
async function craftAndPlaceChest(bot) {
    const have = bot.inventory.items().filter(i => i.name === 'chest').reduce((s, i) => s + i.count, 0)
    const need = 2 - have
    if (need > 0) {
        await convertLogsToPlanks(bot)
        const planks = bot.inventory.items()
            .filter(i => i.name.endsWith('_planks'))
            .reduce((s, i) => s + i.count, 0)
        if (planks < need * 8) { bot.chat(`木板不夠，需要 ${need * 8} 塊製作 ${need} 個箱子`); return false }

        const table = await ensureCraftingTable(bot)
        if (!table) { bot.chat('找不到工作檯，無法製作箱子'); return false }

        const chestItemReg = bot.registry.itemsByName['chest']
        const recipe = chestItemReg ? bot.recipesFor(chestItemReg.id, null, 1, table)[0] : null
        if (!recipe) { bot.chat(`找不到箱子配方（chestItemReg=${!!chestItemReg}，table=${!!table}）`); return false }

        try {
            await bot.craft(recipe, need, table)
            console.log(`[Chest] 合成了 ${need} 個箱子`)
        } catch (e) {
            console.log('[Chest] 合成箱子失敗:', e.message)
            return false
        }
        await reclaimCraftingTable(bot)
    }

    return await _placeChest(bot)
}

function getChests() { return _load() }

module.exports = { setChest, labelChest, readChest, depositToChest, withdrawFromChest, craftAndPlaceChest, getChests }
