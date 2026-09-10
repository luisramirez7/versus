"""Who is tradeable. Cached per symbol in the instruments table."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from versus.db import Database, Instrument
from versus.engine.rules import Rules

from .fmp import FMPClient, Profile

ALLOWED_EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}
ETF_EXCHANGES = ALLOWED_EXCHANGES | {"CBOE"}  # many plain ETFs list on Cboe BZX
LEVERAGED = re.compile(
    r"\b(-?[123]x|ultra(pro|short)?|inverse|leveraged|short|bull|bear|daily target)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Eligibility:
    symbol: str
    eligible: bool
    reason: str | None
    name: str
    price: object | None
    avg_volume: int | None
    is_etf: bool


def evaluate(p: Profile, rules: Rules) -> tuple[bool, str | None]:
    if not p.is_actively_trading:
        return False, "not actively trading"
    if p.is_etf and LEVERAGED.search(p.name):
        return False, "leveraged or inverse ETF"
    if p.exchange not in (ETF_EXCHANGES if p.is_etf else ALLOWED_EXCHANGES):
        return False, f"listed on {p.exchange or 'an unsupported exchange'}, not NYSE/Nasdaq"
    if p.price is None or p.price < rules.min_price:
        return False, f"price below ${rules.min_price}"
    if not p.is_etf and (p.market_cap or 0) < rules.min_market_cap:
        return False, f"market cap below ${rules.min_market_cap / 1e6:.0f}M"
    if (p.avg_volume or 0) < rules.min_avg_volume:
        return False, f"average volume below {rules.min_avg_volume / 1e3:.0f}k shares"
    return True, None


class Universe:
    def __init__(
        self, fmp: FMPClient | None, db: Database, rules: Rules, ttl: timedelta = timedelta(hours=24)
    ):
        self.fmp = fmp
        self.db = db
        self.rules = rules
        self.ttl = ttl

    async def check(self, symbol: str, now: datetime | None = None) -> Eligibility:
        symbol = symbol.upper()
        now = now or datetime.now(UTC)
        async with self.db.session() as s:
            row = await s.get(Instrument, symbol)
            if row and now - row.checked_at < self.ttl:
                return self._from_row(row)
            if self.fmp is None:
                if row:
                    return self._from_row(row)
                return Eligibility(symbol, False, "unknown symbol", symbol, None, None, False)
            profile = await self.fmp.profile(symbol)
            if profile is None:
                elig = Eligibility(symbol, False, "unknown symbol", symbol, None, None, False)
                await self._upsert(s, symbol, profile, elig, now)
                await s.commit()
                return elig
            ok, reason = evaluate(profile, self.rules)
            elig = Eligibility(
                symbol, ok, reason, profile.name, profile.price, profile.avg_volume, profile.is_etf
            )
            await self._upsert(s, symbol, profile, elig, now)
            await s.commit()
            return elig

    async def _upsert(self, s, symbol: str, p: Profile | None, e: Eligibility, now: datetime) -> None:
        row = await s.get(Instrument, symbol)
        if row is None:
            row = Instrument(symbol=symbol, name=e.name, exchange="", checked_at=now)
            s.add(row)
        row.name = e.name
        row.exchange = p.exchange if p else ""
        row.is_etf = p.is_etf if p else False
        row.price = p.price if p else None
        row.market_cap = p.market_cap if p else None
        row.avg_volume = p.avg_volume if p else None
        row.eligible = e.eligible
        row.reason = e.reason
        row.checked_at = now

    @staticmethod
    def _from_row(row: Instrument) -> Eligibility:
        return Eligibility(
            row.symbol, row.eligible, row.reason, row.name, row.price, row.avg_volume, row.is_etf
        )

    async def known(self, symbols: list[str]) -> dict[str, Instrument]:
        async with self.db.session() as s:
            rows = (await s.execute(select(Instrument).where(Instrument.symbol.in_(symbols)))).scalars().all()
            return {r.symbol: r for r in rows}
