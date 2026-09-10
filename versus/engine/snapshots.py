"""Price snapshot persistence and lookup (audit trail + settlement prices)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from versus.db import Database, PriceSnapshot
from versus.market.prices import Observation


class SnapshotWriter:
    """Persists at most one observation per symbol per `min_gap`."""

    def __init__(self, db: Database, min_gap: timedelta = timedelta(minutes=1)) -> None:
        self.db = db
        self.min_gap = min_gap
        self._last: dict[str, datetime] = {}

    async def __call__(self, observations: list[Observation]) -> None:
        rows = []
        for o in observations:
            last = self._last.get(o.symbol)
            if last is not None and o.observed_at - last < self.min_gap:
                continue
            self._last[o.symbol] = o.observed_at
            rows.append(PriceSnapshot(symbol=o.symbol, observed_at=o.observed_at, price=o.price))
        if not rows:
            return
        async with self.db.session() as s:
            s.add_all(rows)
            await s.commit()


async def latest_snapshot_prices(
    db: Database, symbols: list[str], at_or_before: datetime | None = None
) -> dict[str, Decimal]:
    """Most recent stored price per symbol, optionally at or before an instant."""
    if not symbols:
        return {}
    async with db.session() as s:
        q = select(PriceSnapshot.symbol, func.max(PriceSnapshot.observed_at)).where(
            PriceSnapshot.symbol.in_(symbols)
        )
        if at_or_before is not None:
            q = q.where(PriceSnapshot.observed_at <= at_or_before)
        q = q.group_by(PriceSnapshot.symbol)
        latest = (await s.execute(q)).all()
        out: dict[str, Decimal] = {}
        for symbol, ts in latest:
            row = await s.get(PriceSnapshot, (symbol, ts))
            if row is not None:
                out[symbol] = row.price
        return out
