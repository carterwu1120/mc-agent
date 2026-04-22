from __future__ import annotations

from collections import OrderedDict

# How many events/failures/interrupted tasks each skill gets in its prompt.
# Lower budget = shorter prompt = faster LLM, but less context.
SKILL_BUDGETS: dict[str, dict] = {
    "activity_stuck": {"events": 4, "failures": 3, "interrupted": 1},
    "planner":        {"events": 6, "failures": 4, "interrupted": 2},
    "self_task":      {"events": 4, "failures": 3, "interrupted": 2},
    "inventory":      {"events": 3, "failures": 2, "interrupted": 0},
    "respawn":        {"events": 4, "failures": 3, "interrupted": 1},
}
_DEFAULT_BUDGET: dict = {"events": 4, "failures": 3, "interrupted": 1}


def _fmt_pos(pos: dict | None) -> str:
    if not pos:
        return "（無）"
    try:
        return f"({float(pos.get('x', 0)):.0f}, {float(pos.get('y', 0)):.0f}, {float(pos.get('z', 0)):.0f})"
    except Exception:
        return "（無）"


def _trim_text(value, max_len: int = 120) -> str:
    text = str(value or "（無）").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _collapse_events(events: list[dict], limit: int) -> list[dict]:
    collapsed: list[dict] = []
    for item in events or []:
        if len(collapsed) >= limit:
            break
        current = dict(item or {})
        if collapsed:
            prev = collapsed[-1]
            same_signature = (
                prev.get("type") == current.get("type")
                and prev.get("goal") == current.get("goal")
                and prev.get("command") == current.get("command")
                and prev.get("reason") == current.get("reason")
            )
            if same_signature:
                prev["count"] = int(prev.get("count", 1) or 1) + 1
                prev["first_at"] = current.get("at") or prev.get("first_at") or prev.get("at")
                continue
        current["count"] = 1
        current["first_at"] = current.get("at")
        collapsed.append(current)
    return collapsed


def build_recent_events_section(events: list[dict], limit: int = 6) -> str:
    collapsed = _collapse_events(events, limit)
    if not collapsed:
        return ""

    lines = []
    for item in collapsed:
        repeat = f" x{item.get('count')}" if int(item.get("count", 1) or 1) > 1 else ""
        lines.append(
            f"- {item.get('type') or 'event'}{repeat}"
            f" goal={_trim_text(item.get('goal') or '（無）', 48)}"
            f" cmd={_trim_text(item.get('command') or '（無）', 40)}"
            f" reason={_trim_text(item.get('reason') or '（無）', 40)}"
            f" at={item.get('at')}"
        )
    return "\n【最近任務事件】\n" + "\n".join(lines) + "\n"


def build_recent_failures_section(failures: list[dict], limit: int = 4) -> str:
    if not failures:
        return ""

    deduped: OrderedDict[tuple, dict] = OrderedDict()
    for item in failures:
        key = (
            item.get("goal"),
            item.get("command"),
            item.get("activity"),
            item.get("reason"),
        )
        if key not in deduped:
            deduped[key] = dict(item or {})
        if len(deduped) >= limit:
            break

    lines = []
    for item in deduped.values():
        lines.append(
            f"- goal={_trim_text(item.get('goal') or '（無）', 48)}"
            f" cmd={_trim_text(item.get('command') or '（無）', 40)}"
            f" activity={_trim_text(item.get('activity') or '（無）', 24)}"
            f" reason={_trim_text(item.get('reason') or '（無）', 40)}"
            f" at={item.get('at')}"
        )
    return "\n【最近失敗模式】\n" + "\n".join(lines) + "\n"


def build_interrupted_tasks_section(tasks: list[dict], limit: int = 2) -> str:
    if not tasks:
        return ""

    lines = []
    for task in (tasks or [])[:limit]:
        steps = task.get("steps") or []
        current_step = int(task.get("currentStep", 0) or 0)
        remaining = [
            s.get("cmd") for s in steps[current_step:]
            if s.get("status") not in ("done", "failed") and s.get("cmd")
        ] or list(task.get("commands") or [])[current_step:]
        context = task.get("context") or {}
        work_pos = context.get("workPos") or context.get("currentPos")
        lines.append(
            f"- goal={_trim_text(task.get('goal') or '（無）', 48)}"
            f" next={_trim_text((remaining[0] if remaining else '（無）'), 48)}"
            f" interruptedBy={_trim_text(task.get('interruptedBy') or '（無）', 24)}"
            f" workPos={_fmt_pos(work_pos)}"
        )
    return "\n【最近中斷任務摘要】\n" + "\n".join(lines) + "\n"


def build_for_skill(
    skill: str,
    events: list[dict],
    failures: list[dict],
    interrupted: list[dict] | None = None,
) -> str:
    """Build the combined recent-history section for a skill, respecting per-skill budget."""
    budget = SKILL_BUDGETS.get(skill, _DEFAULT_BUDGET)
    parts = []
    section = build_recent_events_section(events, limit=budget["events"])
    if section:
        parts.append(section)
    section = build_recent_failures_section(failures, limit=budget["failures"])
    if section:
        parts.append(section)
    if interrupted is not None and budget.get("interrupted", 0) > 0:
        section = build_interrupted_tasks_section(interrupted, limit=budget["interrupted"])
        if section:
            parts.append(section)
    return "".join(parts)


def build_chests_summary(chests: list[dict], max_chests: int = 4, max_items: int = 4) -> str:
    if not chests:
        return "（無已登記箱子）"

    lines = []
    for chest in chests[:max_chests]:
        contents = chest.get("contents") or []
        content_names = [item.get("name") for item in contents if item.get("name")]
        preview = content_names[:max_items]
        if len(content_names) > max_items:
            preview.append("…")
        lines.append(
            f"- id={chest['id']} label={chest.get('label', '未分類')}"
            f" freeSlots={chest.get('freeSlots', '?')}"
            f" contents={preview}"
        )
    return "\n".join(lines)
