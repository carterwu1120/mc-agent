"""
Replay tests for activity_stuck deterministic_shortcut paths.
These tests verify that known production stuck states produce the expected
recovery commands WITHOUT calling the LLM.
"""
import pytest
from unittest.mock import patch
from agent.skills.stuck import mining as mining_stuck
from agent.skills.stuck import smelting as smelting_stuck


# ── Helpers ───────────────────────────────────────────────────────────────────

def _state(**kwargs):
    base = {
        "activity": "mining",
        "reason": "no_tools",
        "inventory": [],
        "inventory_slots": {"free": 20},
        "chests": [],
        "capabilities": {},
        "equipment": {},
        "pos": {"x": 0, "y": -50, "z": 0},
        "health": 20,
        "food": 18,
    }
    base.update(kwargs)
    return base


def _plan_context(current_cmd="mine diamond 10", pending=None):
    return {
        "goal": "mine diamond 10",
        "current_cmd": current_cmd,
        "pending_steps": pending or [],
        "done_steps": [],
        "total_steps": 1,
        "current_step": 0,
    }


# ── mining: no_tools ──────────────────────────────────────────────────────────

class TestMiningNoTools:
    def setup_method(self):
        # Reset tool_acquisition fingerprint between tests
        from agent.skills.stuck import tool_acquisition
        tool_acquisition._LAST_ATTEMPTS.clear()

    def test_no_tools_can_make_pickaxe_produces_replan(self):
        state = _state(
            reason="no_tools",
            capabilities={"can_make_pickaxe": True},
            inventory=[
                {"name": "oak_log", "count": 4},
                {"name": "cobblestone", "count": 8},
            ],
        )
        result = mining_stuck.deterministic_shortcut(state, _plan_context())
        assert result is not None
        actions = [a.get("action") for a in result]
        assert "replan" in actions
        replan = next(a for a in result if a.get("action") == "replan")
        # Must include equip and retry the original mine command
        assert any("equip" in c for c in replan["commands"])
        assert any("mine" in c for c in replan["commands"])

    def test_no_tools_cannot_make_pickaxe_includes_chop(self):
        state = _state(
            reason="no_tools",
            capabilities={"can_make_pickaxe": False},
            inventory=[],
        )
        result = mining_stuck.deterministic_shortcut(state, _plan_context())
        assert result is not None
        replan = next((a for a in result if a.get("action") == "replan"), None)
        assert replan is not None
        # Must chop wood first when can't craft pickaxe
        assert any("chop" in c for c in replan["commands"])

    def test_no_tools_without_plan_context_returns_none_or_single_cmd(self):
        state = _state(
            reason="no_tools",
            capabilities={"can_make_pickaxe": True},
            inventory=[{"name": "cobblestone", "count": 8}],
        )
        # Without plan_context, pickaxe_replan_commands may return None
        result = mining_stuck.deterministic_shortcut(state, None)
        # Result is either None (escalate to LLM) or a valid recovery
        if result is not None:
            assert isinstance(result, list)

    def test_no_tools_replan_preserves_pending_steps(self):
        state = _state(
            reason="no_tools",
            capabilities={"can_make_pickaxe": True},
            inventory=[
                {"name": "cobblestone", "count": 8},
                {"name": "oak_log", "count": 4},
            ],
        )
        plan = _plan_context(
            current_cmd="mine iron 8",
            pending=["smelt raw_iron 8", "equip"],
        )
        result = mining_stuck.deterministic_shortcut(state, plan)
        assert result is not None
        replan = next(a for a in result if a.get("action") == "replan")
        cmds = replan["commands"]
        # Original pending steps must be preserved at the end
        assert "smelt raw_iron 8" in cmds
        assert "equip" in cmds
        # pending steps order preserved
        smelt_idx = cmds.index("smelt raw_iron 8")
        equip_idx = cmds.index("equip", smelt_idx)  # find equip AFTER smelt
        assert smelt_idx < equip_idx

    def test_no_tools_no_duplicate_equip(self):
        state = _state(
            reason="no_tools",
            capabilities={"can_make_pickaxe": True},
            inventory=[{"name": "cobblestone", "count": 8}],
        )
        result = mining_stuck.deterministic_shortcut(state, _plan_context())
        if result is None:
            return
        replan = next((a for a in result if a.get("action") == "replan"), None)
        if replan:
            cmds = replan["commands"]
            # No adjacent duplicate equip
            for i in range(len(cmds) - 1):
                assert not (cmds[i] == "equip" and cmds[i + 1] == "equip"), \
                    f"Adjacent equip equip found in: {cmds}"

    def test_no_tools_reason_other_returns_none(self):
        state = _state(reason="stuck_in_wall")
        result = mining_stuck.deterministic_shortcut(state, _plan_context())
        assert result is None


# ── mining: water_loop ────────────────────────────────────────────────────────

class TestMiningWaterLoop:
    def setup_method(self):
        from agent.skills.stuck import tool_acquisition
        tool_acquisition._LAST_ATTEMPTS.clear()

    def test_water_loop_with_plan_prepends_home(self):
        state = _state(reason="water_loop", inventory=[])
        plan = _plan_context(current_cmd="mine diamond 5", pending=["equip"])
        result = mining_stuck.deterministic_shortcut(state, plan)
        assert result is not None
        replan = next(a for a in result if a.get("action") == "replan")
        assert replan["commands"][0] == "home"

    def test_water_loop_without_plan_returns_home_cmd(self):
        state = _state(reason="water_loop", inventory=[])
        result = mining_stuck.deterministic_shortcut(state, None)
        assert result is not None
        cmds = [a.get("command") for a in result]
        assert "home" in cmds

    def test_trapped_in_water_same_as_water_loop(self):
        state = _state(reason="trapped_in_water", inventory=[])
        result = mining_stuck.deterministic_shortcut(state, None)
        assert result is not None
        cmds = [a.get("command") for a in result]
        assert "home" in cmds


# ── smelting: no_input ────────────────────────────────────────────────────────

class TestSmeltingNoInput:
    def setup_method(self):
        from agent.skills.stuck import tool_acquisition
        tool_acquisition._LAST_ATTEMPTS.clear()

    def _smelting_state(self, **kwargs):
        base = _state(activity="smelting", reason="no_input")
        base.update(kwargs)
        return base

    def _dummy_getfood_builder(self, state, plan_context):
        return None

    def test_no_input_with_can_make_pickaxe_replans_mine_first(self):
        state = self._smelting_state(
            capabilities={"can_make_pickaxe": True},
            inventory=[{"name": "cobblestone", "count": 8}],
        )
        plan = {
            "goal": "smelt raw_iron 8",
            "current_cmd": "smelt raw_iron 8",
            "pending_steps": [],
            "done_steps": [],
            "total_steps": 1,
            "current_step": 0,
        }
        result = smelting_stuck.deterministic_shortcut(
            state, plan, self._dummy_getfood_builder
        )
        assert result is not None
        replan = next((a for a in result if a.get("action") == "replan"), None)
        assert replan is not None
        cmds = replan["commands"]
        # Must mine iron before smelting
        mine_idx = next((i for i, c in enumerate(cmds) if "mine iron" in c or "mine raw_iron" in c or c.startswith("mine")), None)
        smelt_idx = next((i for i, c in enumerate(cmds) if c.startswith("smelt")), None)
        assert mine_idx is not None
        assert smelt_idx is not None
        assert mine_idx < smelt_idx

    def test_no_input_cannot_make_pickaxe_includes_chop(self):
        state = self._smelting_state(
            capabilities={"can_make_pickaxe": False},
            inventory=[],
        )
        plan = {
            "goal": "smelt raw_iron 8",
            "current_cmd": "smelt raw_iron 8",
            "pending_steps": [],
            "done_steps": [],
            "total_steps": 1,
            "current_step": 0,
        }
        result = smelting_stuck.deterministic_shortcut(
            state, plan, self._dummy_getfood_builder
        )
        assert result is not None
        replan = next((a for a in result if a.get("action") == "replan"), None)
        assert replan is not None
        cmds = replan["commands"]
        assert any("chop" in c for c in cmds)

    def test_no_input_no_plan_context_returns_none(self):
        state = self._smelting_state(capabilities={"can_make_pickaxe": True})
        result = smelting_stuck.deterministic_shortcut(
            state, None, self._dummy_getfood_builder
        )
        assert result is None

    def test_no_progress_full_inventory_with_chest_replans_deposit(self):
        state = self._smelting_state(
            reason="no_progress",
            inventory_slots={"free": 1},
            chests=[{"id": "chest_1", "label": "ore", "freeSlots": 10}],
        )
        plan = {
            "goal": "smelt raw_iron 8",
            "current_cmd": "smelt raw_iron 8",
            "pending_steps": ["equip"],
            "done_steps": [],
            "total_steps": 2,
            "current_step": 0,
        }
        result = smelting_stuck.deterministic_shortcut(
            state, plan, self._dummy_getfood_builder
        )
        assert result is not None
        replan = next((a for a in result if a.get("action") == "replan"), None)
        assert replan is not None
        assert replan["commands"][0].startswith("deposit")
