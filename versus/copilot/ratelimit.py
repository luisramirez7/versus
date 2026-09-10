"""Per-user sliding-window rate limit for /ask. In memory: a restart forgives everyone, which is fine."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: float = 3600.0, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def acquire(self, key: int) -> float:
        """Count one call and return 0.0, or return the seconds until the next call is allowed."""
        if self.limit <= 0:
            return 0.0
        now = self.clock()
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            return hits[0] + self.window_s - now
        hits.append(now)
        return 0.0

    def remaining(self, key: int) -> int:
        now = self.clock()
        return max(0, self.limit - sum(1 for t in self._hits.get(key, ()) if t > now - self.window_s))
