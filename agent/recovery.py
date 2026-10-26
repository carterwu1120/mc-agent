"""Bounded, single-flight recovery; no game/model dependencies at import time."""
import asyncio
from collections import deque
import json
import math
import time
import uuid


# Failures that say nothing about the strategy: the planner never answered.
PROVIDER_ERRORS = {"TimeoutError", "CLIProviderError"}
PLANNER_UNAVAILABLE = "planner_unavailable"


class RecoverySupervisor:
    def __init__(self, *, pause_timeout=5, decision_timeout=60, clock=time.monotonic,
                 provider_retry_delay=120, provider_max_retries=3):
        if not all(math.isfinite(value) and value > 0 for value in (pause_timeout, decision_timeout)):
            raise ValueError('Recovery timeouts must be positive finite seconds')
        self.pause_timeout = pause_timeout
        self.decision_timeout = decision_timeout
        self.clock = clock
        self.phase = "executing"
        self.reason = None
        self.deadline = None
        self.expected = None
        self.revision = 0
        self._ack = None
        self._request_id = None
        self._decision = None
        self._idle = None
        self.wait_kind = None
        self.wait_snapshot = None
        self._episodes = deque(maxlen=3)
        self.history = deque(maxlen=6)
        self.provider_retry_delay = provider_retry_delay
        self.provider_max_retries = provider_max_retries
        # Consecutive recoveries lost to an unreachable planner; survives invalidate() so
        # automatic retries stay bounded, cleared by any real decision.
        self.provider_failures = 0
        self.retry_at = None

    @property
    def busy(self):
        return self.phase in {"stopping", "waiting_ai"}

    @property
    def held(self):
        return self.phase in {"waiting_player", "waiting_for_change"}

    def status(self):
        return {"phase": self.phase, "reason": self.reason,
                "remaining_seconds": max(0, round(self.deadline - self.clock())) if self.deadline else None,
                "expected_outcome": self.expected,
                "waiting_for": self.wait_kind}

    def hold_for_change(self, kind: str, snapshot: dict, reason: str) -> None:
        """Pause until a domain-specific observer finds a meaningful state change."""
        self.phase = "waiting_for_change"
        self.reason = reason
        self.deadline = None
        self.expected = None
        self.wait_kind = kind
        self.wait_snapshot = dict(snapshot)

    def release_change_wait(self) -> None:
        """Release a semantic wait without treating elapsed time as progress."""
        if self.phase != "waiting_for_change":
            return
        self.revision += 1
        self.phase = "executing"
        self.reason = self.deadline = self.expected = self._idle = None
        self.wait_kind = None
        self.wait_snapshot = None

    def invalidate(self):
        """A new explicit task, stop, death or disconnect supersedes old replies."""
        self.revision += 1
        if self._decision and not self._decision.done():
            self._decision.cancel()
        if self._ack and not self._ack.done():
            self._ack.cancel()
        self.phase = "executing"
        self.reason = self.deadline = self.expected = self._idle = None
        self.wait_kind = None
        self.wait_snapshot = None
        self.retry_at = None
        self._episodes.clear()
        self.history.clear()

    def provider_retry_due(self) -> bool:
        return self.phase == "waiting_player" and self.retry_at is not None and self.clock() >= self.retry_at

    def note_progress(self):
        """A long-running step finished in the world, so earlier episodes were resolved.

        Only the rolling episode count resets; strategy history stays, so an identical
        failed strategy is still rejected. Instant steps (back, equip) do not count, or a
        stuck loop could renew its budget forever.
        """
        self._episodes.clear()

    def acknowledge(self, state):
        if state.get("request_id") == self._request_id and self._ack and not self._ack.done():
            self._ack.set_result(state)

    def unexpected_idle(self, state, task, *, timeout=15):
        if self.busy or self.held or not task or task.get("status") != "running" or state.get("mc_connected") is False:
            self._idle = None
            return None
        if state.get("activity") != "idle":
            self._idle = None
            return None
        key = (task.get("id"), task.get("currentStep"))
        if self._idle is None or self._idle[0] != key:
            self._idle = (key, self.clock())
        if self.clock() - self._idle[1] < timeout:
            return None
        return {**state, "type": "activity_stuck", "reason": "unexpected_idle",
                "detail": f"未完成任務持續 {timeout} 秒沒有活動接手；不是正常待命。"}

    async def recover(self, state, ws, *, decide, apply, blocked):
        if self.busy or self.held:
            return
        self.revision += 1
        revision = self.revision
        self.phase = "stopping"
        self.reason = state.get("reason", "unknown")
        self.deadline = self.clock() + self.pause_timeout
        self._request_id = uuid.uuid4().hex
        self._ack = asyncio.get_running_loop().create_future()
        error = "recovery_failed"
        provider_errors = 0
        try:
            await asyncio.wait_for(ws.send(json.dumps({"command": "suspend", "request_id": self._request_id})), self.pause_timeout)
            ack = await asyncio.wait_for(self._ack, self.pause_timeout)
            if not ack.get("stopped"):
                raise RuntimeError("stop_not_confirmed")
            # Decisions use fresh world state, while retaining the failed goal,
            # stack and evidence from before suspension.
            state = {**state, **{key: ack[key] for key in ("pos", "inventory", "equipment", "health", "food", "nearby", "entities") if key in ack}}
            now = self.clock()
            while self._episodes and now - self._episodes[0] >= 300:
                self._episodes.popleft()
            checkpoint = state.get('reason') == 'objective_incomplete'
            if not checkpoint and len(self._episodes) >= 3:
                raise RuntimeError("recovery_budget_exhausted")
            if not checkpoint:
                self._episodes.append(now)
            self.phase = "waiting_ai"
            fingerprint = json.dumps({
                "inventory": sorted((i["name"], i["count"]) for i in state.get("inventory", [])),
                "goal": (state.get("plan_context") or {}).get("goal"),
                "reason": state.get("reason"),
                "checkpoint_position": state.get('pos') if checkpoint else None,
                "nearby": state.get("nearby") or {},
                "navigation": {
                    key: (state.get("navigation") or {}).get(key)
                    for key in ("status", "reason")
                },
            }, sort_keys=True)
            for attempt in range(2):
                self.deadline = self.clock() + self.decision_timeout
                try:
                    self._decision = asyncio.create_task(decide({**state, "recovery_history": list(self.history),
                                                               "decision_error": error if attempt else None}))
                    result = await asyncio.wait_for(self._decision, self.decision_timeout)
                    if revision != self.revision:
                        return
                    if result.get("action") == "wait":
                        error = result["text"]
                        break
                    commands = result["commands"]
                    if any(h.get("fingerprint") == fingerprint and h.get("commands") == commands for h in self.history):
                        raise ValueError("same_failed_strategy: change the approach or explain why player help is needed")
                    self.history.append({"reason": self.reason, "commands": commands,
                                         "expected_outcome": result["expected_outcome"], "fingerprint": fingerprint})
                    self.expected = result["expected_outcome"]
                    await apply(result)
                    self.provider_failures = 0
                    self.phase = "executing"
                    self.deadline = None
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error = type(exc).__name__ + ": " + str(exc)[:200]
                    if type(exc).__name__ in PROVIDER_ERRORS:
                        provider_errors += 1
                    self.history.append({"reason": "decision_failed", "error": error})
        except asyncio.CancelledError:
            if revision == self.revision:
                self.phase = "waiting_player"
                self.reason = "recovery_cancelled"
            return
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)[:200]
        finally:
            if revision == self.revision:
                self.deadline = None
        if revision == self.revision:
            self.phase = "waiting_player"
            if provider_errors == 2:
                # Both attempts got no answer; retry later instead of waiting on the player.
                self.provider_failures += 1
                error = f"{PLANNER_UNAVAILABLE}: {error}"
                retry = self.provider_failures <= self.provider_max_retries
                self.retry_at = self.clock() + self.provider_retry_delay if retry else None
            else:
                self.provider_failures = 0
                self.retry_at = None
            self.reason = error
            await blocked(error)
