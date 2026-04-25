from __future__ import annotations
import asyncio
import time

from agent.brain.base import LLMClient

_RETRYABLE_PATTERNS = ("429", "rate limit", "quota", "503", "unavailable", "high demand", "try again")


class TokenBucket:
    """Token bucket for client-side rate limiting."""

    def __init__(self, rpm: int, burst: int) -> None:
        self._refill_rate = rpm / 60.0  # tokens per second
        self._capacity = float(burst)
        self._tokens = float(burst)     # start full
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._refill_rate
                await asyncio.sleep(wait)


class RateLimitedLLMClient(LLMClient):
    """Wraps any LLMClient with token-bucket rate limiting and exponential backoff retry."""

    def __init__(self, inner: LLMClient, rpm: int = 60, burst: int = 5, max_retries: int = 4) -> None:
        self._inner = inner
        self._bucket = TokenBucket(rpm=rpm, burst=burst)
        self._max_retries = max_retries

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        await self._bucket.acquire()
        for attempt in range(1, self._max_retries + 1):
            t0 = time.monotonic()
            try:
                result = await self._inner.chat(messages, system=system)
                elapsed = time.monotonic() - t0
                print(f"[LLM] ok latency={elapsed:.1f}s")
                return result
            except Exception as e:
                elapsed = time.monotonic() - t0
                msg = str(e).lower()
                is_retryable = any(p in msg for p in _RETRYABLE_PATTERNS)
                if not is_retryable or attempt == self._max_retries:
                    print(f"[LLM] error latency={elapsed:.1f}s: {e}")
                    raise
                delay = min(2 ** attempt, 60)  # 2s, 4s, 8s, 16s … capped at 60s
                print(f"[RateLimiter] LLM 暫時不可用 (attempt {attempt}/{self._max_retries}), retry in {delay}s: {e}")
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")
