const _hazards = []

function _now() {
    return Date.now()
}

function _clean(pos) {
    if (!pos) return null
    return {
        x: Math.floor(pos.x),
        y: Math.floor(pos.y),
        z: Math.floor(pos.z),
    }
}

function remember(kind, pos, ttlMs = 120000, radius = 6) {
    const clean = _clean(pos)
    if (!clean || !kind) return
    const expiresAt = _now() + ttlMs
    for (const hazard of _hazards) {
        if (hazard.kind !== kind) continue
        const same =
            Math.abs(hazard.pos.x - clean.x) <= 1 &&
            Math.abs(hazard.pos.y - clean.y) <= 1 &&
            Math.abs(hazard.pos.z - clean.z) <= 1
        if (same) {
            hazard.expiresAt = expiresAt
            hazard.radius = Math.max(hazard.radius, radius)
            return
        }
    }
    _hazards.push({ kind, pos: clean, expiresAt, radius })
}

function prune() {
    const now = _now()
    for (let i = _hazards.length - 1; i >= 0; i--) {
        if (_hazards[i].expiresAt <= now) _hazards.splice(i, 1)
    }
}

function isNear(pos, kind, radius = null) {
    const clean = _clean(pos)
    if (!clean) return false
    prune()
    return _hazards.some(hazard => {
        if (kind && hazard.kind !== kind) return false
        const dx = clean.x - hazard.pos.x
        const dy = clean.y - hazard.pos.y
        const dz = clean.z - hazard.pos.z
        const limit = radius ?? hazard.radius
        return (dx * dx) + (dy * dy) + (dz * dz) <= limit * limit
    })
}

module.exports = { remember, isNear, prune }
