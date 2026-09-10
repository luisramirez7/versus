"""In-memory last-price map fed by one poller, with "wait for a fresh price" semantics.

The fill rule is: a market order fills at the first price observed at least
`fill_delay_s` after the order was received. `wait_for_fresh` is that primitive.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from .fmp import FMPClient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Observation:
    symbol: str
    price: Decimal
    observed_at: datetime  # server time when we saw it (UTC)


class PriceService:
    def __init__(
        self,
        fmp: FMPClient | None,
        interval_s: float = 5.0,
        on_observations: Callable[[list[Observation]], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.fmp = fmp
        self.interval_s = interval_s
        self.on_observations = on_observations
        self.clock = clock
        self._latest: dict[str, Observation] = {}
        self._tracked: set[str] = set()
        self._waiters: dict[str, list[asyncio.Future[Observation]]] = {}
        self._task: asyncio.Task | None = None

    # ----- reads -----
    def latest(self, symbol: str) -> Observation | None:
        return self._latest.get(symbol)

    def track(self, symbols: Iterable[str]) -> None:
        self._tracked.update(s.upper() for s in symbols)

    def untrack(self, symbols: Iterable[str]) -> None:
        for s in symbols:
            self._tracked.discard(s.upper())

    @property
    def tracked(self) -> set[str]:
        return set(self._tracked)

    async def wait_for_fresh(self, symbol: str, after: datetime, timeout: float) -> Observation:
        """Return the first observation of `symbol` made at or after `after`."""
        symbol = symbol.upper()
        self._tracked.add(symbol)
        obs = self._latest.get(symbol)
        if obs is not None and obs.observed_at >= after:
            return obs
        fut: asyncio.Future[Observation] = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(symbol, []).append((after, fut))  # type: ignore[arg-type]
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            lst = self._waiters.get(symbol)
            if lst:
                self._waiters[symbol] = [w for w in lst if w[1] is not fut]  # type: ignore[index]

    # ----- writes -----
    def ingest(self, observations: Iterable[Observation]) -> None:
        for obs in observations:
            self._latest[obs.symbol] = obs
            for after, fut in list(self._waiters.get(obs.symbol, [])):  # type: ignore[misc]
                if not fut.done() and obs.observed_at >= after:
                    fut.set_result(obs)

    async def poll_once(self) -> list[Observation]:
        if self.fmp is None:
            return []
        symbols = sorted(self._tracked | set(self._waiters))
        if not symbols:
            return []
        try:
            quotes = await self.fmp.batch_quotes(symbols)
        except Exception:  # network hiccups must not kill the poller
            log.exception("price poll failed")
            return []
        now = self.clock()
        observations = [Observation(q.symbol, q.price, now) for q in quotes.values()]
        self.ingest(observations)
        if self.on_observations and observations:
            try:
                await self.on_observations(observations)
            except Exception:
                log.exception("on_observations failed")
        return observations

    async def run(self, is_active: Callable[[], bool]) -> None:
        """Poll every interval while `is_active()`; otherwise idle at a slow cadence."""
        while True:
            if is_active() or self._waiters:
                await self.poll_once()
                await asyncio.sleep(self.interval_s)
            else:
                await asyncio.sleep(max(self.interval_s, 30.0))

    def start(self, is_active: Callable[[], bool]) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(is_active), name="price-poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
