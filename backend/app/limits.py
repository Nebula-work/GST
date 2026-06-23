"""Lightweight, dependency-free abuse controls.

The reconcile API is public and unauthenticated, so a single client must not be
able to flood the expensive parse/reconcile or run many jobs at once. These
controls are sized for the single-worker deployment (state is per-process, in
memory) and gate the CPU-bound work — they run after the request body has been
read, so the raw upload size is bounded at the edge by nginx (client_max_body_size),
not here. nginx also rate-limits per IP in production; this is the portable,
always-on backstop for the work itself.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque


class SlidingWindowRateLimiter:
    """Per-key sliding-window limiter: at most `max_requests` per `window`.

    `max_requests <= 0` disables it. Memory is bounded: at most `max_keys`
    distinct keys are tracked (oldest evicted first), so a flood of distinct
    keys — e.g. spoofed client IPs — cannot grow the table without limit.
    """

    def __init__(self, max_requests: int, window_seconds: float,
                 max_keys: int = 20_000) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: "OrderedDict[str, deque[float]]" = OrderedDict()

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.max_requests <= 0:
            return True
        now = time.monotonic() if now is None else now
        dq = self._hits.get(key)
        if dq is None:
            dq = deque()
            self._hits[key] = dq
        self._hits.move_to_end(key)
        cutoff = now - self.window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= self.max_requests:
            return False
        dq.append(now)
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)
        return True


class ConcurrencyGate:
    """Bounds how many expensive jobs run at once. Non-blocking: callers that

    can't get a slot are told to retry rather than queueing (which would pile up
    connections). Safe on a single event loop — acquire/release never await.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self.active = 0

    def try_acquire(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def release(self) -> None:
        if self.active > 0:
            self.active -= 1
