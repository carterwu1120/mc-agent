// 記錄已埋垃圾的位置，讓 mining 跳過這些格子避免重新挖開
const _buried = new Set()

function _key(pos) {
    return `${Math.floor(pos.x)},${Math.floor(pos.y)},${Math.floor(pos.z)}`
}

function markBuried(pos) {
    const key = _key(pos)
    _buried.add(key)
    // Minecraft 掉落物 5 分鐘後消失，解除保護
    setTimeout(() => _buried.delete(key), 5 * 60 * 1000)
}

function isBuried(pos) {
    return _buried.has(_key(pos))
}

module.exports = { markBuried, isBuried }
