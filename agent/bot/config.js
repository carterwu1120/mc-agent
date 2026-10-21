const fs = require('node:fs')
const path = require('node:path')

function loadProjectEnv(file = path.resolve(__dirname, '../../.env')) {
    if (fs.existsSync(file)) process.loadEnvFile(file)
}

function connectionOptions(env = process.env) {
    const port = Number(env.MC_PORT || 25565)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
        throw new Error('MC_PORT must be an integer between 1 and 65535 (use the port shown by Open to LAN)')
    }
    const auth = env.MC_AUTH || 'offline'
    if (!['offline', 'microsoft'].includes(auth)) throw new Error('MC_AUTH must be offline or microsoft')
    return {
        host: env.MC_HOST || 'localhost', port,
        username: env.MC_USERNAME || 'Agent', auth,
        version: env.MC_VERSION === 'auto' ? false : (env.MC_VERSION || '1.20.1'),
    }
}

module.exports = { loadProjectEnv, connectionOptions }

if (require.main === module) {
    loadProjectEnv()
    console.log(JSON.stringify({
        ...connectionOptions(),
        forge: String(process.env.MC_FORGE || 'true').toLowerCase() === 'true',
        adaptiveNavigation: process.env.MC_ADAPTIVE_NAVIGATION === 'true',
    }, null, 2))
    console.log('Configuration only: no Minecraft connection or model call was made.')
}
