from datetime import datetime, timedelta

from agent.context_builder import MEMORY_MAX_AGE, build_for_skill

NOW = datetime(2026, 10, 25, 3, 40, 0)


def at(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def test_stale_failures_do_not_reach_the_prompt():
    # Seen live: failures from 39 minutes earlier (before /tp permission was granted)
    # made the planner skip surface even though it worked by then.
    failures = [
        {"goal": "mine diamond 10", "command": "mine iron 1", "reason": "replanned:cannot_reach_target", "at": at(2)},
        {"goal": "給你食物了欸", "command": "surface", "reason": "replanned:timeout", "at": at(39)},
    ]
    section = build_for_skill("planner", [], failures, now=NOW)
    assert "mine iron 1" in section
    assert "surface" not in section


def test_recent_and_undated_entries_are_kept():
    events = [{"type": "replan", "reason": "no_blocks", "at": at(5)}, {"type": "interrupt", "reason": "recovery"}]
    section = build_for_skill("planner", events, [], now=NOW)
    assert "no_blocks" in section and "interrupt" in section


def test_timezone_aware_timestamps_are_compared_in_utc():
    stale = {"goal": "g", "command": "explore animals", "reason": "x", "at": (NOW - MEMORY_MAX_AGE * 2).isoformat() + "+00:00"}
    assert "explore animals" not in build_for_skill("planner", [], [stale], now=NOW)
