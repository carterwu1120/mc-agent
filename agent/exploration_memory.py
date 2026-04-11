"""
Spatial memory — 記錄 bot 在世界中找到資源的位置，供 self_task 規劃時使用。

記錄類型：
- ore_finds   : 挖到礦的位置（有效期 7 天）
- forest_finds: 砍到樹的位置（有效期 3 天）
- animal_areas: 看到/獵殺動物的位置（有效期 6 小時，動物會移動）
"""
from __future__ import annotations

import json
import os
import pathlib
import time

_DATA_DIR = os.environ.get('BOT_DATA_DIR', str(pathlib.Path(__file__).parent / 'data'))
_DATA_FILE = pathlib.Path(_DATA_DIR) / 'exploration_memory.json'

_ORE_TTL_HOURS    = 7 * 24   # 礦不會消失，但採完就沒了，所以設 7 天後過期
_FOREST_TTL_HOURS = 3 * 24   # 樹會重生
_ANIMAL_TTL_HOURS = 6         # 動物移動快，6 小時過期


# ── I/O ────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        return json.loads(_DATA_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {"ore_finds": [], "forest_finds": [], "animal_areas": []}


def _save(data: dict) -> None:
    _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def _clean_pos(pos: dict) -> dict:
    return {
        "x": round(float(pos.get("x", 0)), 1),
        "y": round(float(pos.get("y", 0)), 1),
        "z": round(float(pos.get("z", 0)), 1),
    }


def _now() -> float:
    return time.time()


def _hours_ago(timestamp: float) -> str:
    hours = (_now() - timestamp) / 3600
    if hours < 1:
        return f"{int(hours * 60)}分鐘"
    return f"{hours:.1f}小時"


# ── Write ───────────────────────────────────────────────────────────

def record_ore(ore_type: str, pos: dict, count: int = 0, dimension: str = "overworld") -> None:
    """記錄挖到礦的位置。"""
    data = _load()
    data.setdefault("ore_finds", []).append({
        "type": ore_type,
        "pos": _clean_pos(pos),
        "count": count,
        "dimension": dimension,
        "timestamp": _now(),
    })
    data["ore_finds"] = data["ore_finds"][-100:]
    _save(data)
    print(f"[ExplMem] 記錄礦物: {ore_type} x{count} @ ({pos.get('x',0):.0f}, {pos.get('y',0):.0f}, {pos.get('z',0):.0f})")


def record_forest(pos: dict) -> None:
    """記錄砍樹區域。"""
    data = _load()
    data.setdefault("forest_finds", []).append({
        "pos": _clean_pos(pos),
        "timestamp": _now(),
    })
    data["forest_finds"] = data["forest_finds"][-30:]
    _save(data)
    print(f"[ExplMem] 記錄森林: @ ({pos.get('x',0):.0f}, {pos.get('y',0):.0f}, {pos.get('z',0):.0f})")


def record_animal_area(pos: dict) -> None:
    """記錄看到動物的區域。"""
    data = _load()
    data.setdefault("animal_areas", []).append({
        "pos": _clean_pos(pos),
        "timestamp": _now(),
    })
    data["animal_areas"] = data["animal_areas"][-30:]
    _save(data)
    print(f"[ExplMem] 記錄動物區: @ ({pos.get('x',0):.0f}, {pos.get('y',0):.0f}, {pos.get('z',0):.0f})")


# ── Read ────────────────────────────────────────────────────────────

def get_ore_sites(ore_type: str | None = None, max_age_hours: float = _ORE_TTL_HOURS) -> list[dict]:
    """返回最近找到的礦物位置（最新優先）。ore_type=None 返回所有種類。"""
    cutoff = _now() - max_age_hours * 3600
    return sorted(
        [
            s for s in _load().get("ore_finds", [])
            if s.get("timestamp", 0) >= cutoff
            and (ore_type is None or s.get("type") == ore_type)
        ],
        key=lambda s: s.get("timestamp", 0),
        reverse=True,
    )


def get_forest_sites(max_age_hours: float = _FOREST_TTL_HOURS) -> list[dict]:
    """返回最近砍樹的位置（最新優先）。"""
    cutoff = _now() - max_age_hours * 3600
    return sorted(
        [s for s in _load().get("forest_finds", []) if s.get("timestamp", 0) >= cutoff],
        key=lambda s: s.get("timestamp", 0),
        reverse=True,
    )


def get_animal_sites(max_age_hours: float = _ANIMAL_TTL_HOURS) -> list[dict]:
    """返回最近看到動物的位置（最新優先）。"""
    cutoff = _now() - max_age_hours * 3600
    return sorted(
        [s for s in _load().get("animal_areas", []) if s.get("timestamp", 0) >= cutoff],
        key=lambda s: s.get("timestamp", 0),
        reverse=True,
    )


# ── Summary for LLM ────────────────────────────────────────────────

def summary_for_prompt() -> str:
    """給 LLM 看的已知資源位置摘要。若無記錄則返回空字串。"""
    lines = []

    for ore_type in ("diamond", "iron", "gold", "coal", "copper"):
        sites = get_ore_sites(ore_type, max_age_hours=48)
        if sites:
            best = sites[0]
            p = best["pos"]
            lines.append(
                f"- {ore_type}：上次在 ({p['x']:.0f}, {p['y']:.0f}, {p['z']:.0f})"
                f" 找到 {best.get('count', '?')} 個（{_hours_ago(best['timestamp'])}前）"
            )

    forests = get_forest_sites()
    if forests:
        p = forests[0]["pos"]
        lines.append(
            f"- 森林/樹木：上次在 ({p['x']:.0f}, {p['y']:.0f}, {p['z']:.0f})"
            f"（{_hours_ago(forests[0]['timestamp'])}前）"
        )

    animals = get_animal_sites()
    if animals:
        p = animals[0]["pos"]
        lines.append(
            f"- 動物區域：上次在 ({p['x']:.0f}, {p['y']:.0f}, {p['z']:.0f})"
            f"（{_hours_ago(animals[0]['timestamp'])}前）"
        )

    return "\n".join(lines)
