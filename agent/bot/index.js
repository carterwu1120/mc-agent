const mineflayer = require('mineflayer')
const minecraftProtocolForge = require('minecraft-protocol-forge')

// ── 建立 bot ──────────────────────────────────────────────
const bot = mineflayer.createBot({
    host: 'localhost',
    port: 25565,
    username: 'Agent',       // bot 的名字，隨便取
    version: '1.20.1',       // 要跟你的 Minecraft 版本一樣
    auth: 'offline',         // 單人世界 LAN 不需要正版驗證
    forgeHandshake: true,    // 處理 Forge 的 FML handshake
})

// Forge 握手支援
minecraftProtocolForge.forgeHandshake(bot._client, { forge: true })

// ── 事件：成功進入世界 ─────────────────────────────────────
// Mineflayer 是「事件驅動」的
// 意思是：不是你去問「現在狀態如何？」
// 而是「有事情發生的時候，自動通知你」
bot.once('spawn', () => {
    console.log(`[Bot] 進入世界！位置：${JSON.stringify(bot.entity.position)}`)
    console.log(`[Bot] 血量：${bot.health}  飢餓：${bot.food}`)
    console.log(`[Bot] 世界裡的玩家：${Object.keys(bot.players).join(', ')}`)
})

// ── 事件：收到聊天訊息 ─────────────────────────────────────
bot.on('chat', (username, message) => {
    console.log(`[Chat] ${username}: ${message}`)

    // 先做最簡單的：複誦對方說的話
    if (username === bot.username) return  // 不要回應自己
    bot.chat(`你說了：${message}`)
})

// ── 事件：錯誤處理 ────────────────────────────────────────
bot.on('error', (err) => {
    console.error(`[Error] ${err.message}`)
})

bot.on('kicked', (reason) => {
    console.log(`[Kicked] ${reason}`)
})

bot.on('end', () => {
    console.log('[Bot] 連線結束')
})
