"""Wires the engine together. One object the cogs and scheduler share."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from versus.config import Settings
from versus.db import Database
from versus.engine.fills import FillEngine
from versus.engine.rounds import RoundService
from versus.engine.rules import Rules
from versus.engine.snapshots import SnapshotWriter
from versus.market.clock import MarketCalendar
from versus.market.fmp import FMPClient
from versus.market.prices import PriceService
from versus.market.universe import Universe

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    db: Database
    fmp: FMPClient
    calendar: MarketCalendar
    prices: PriceService
    universe: Universe
    rounds: RoundService
    engine: FillEngine

    @classmethod
    async def build(cls, settings: Settings) -> Services:
        db = Database(settings.database_url)
        await db.create_all()
        fmp = FMPClient(settings.fmp_api_key, settings.fmp_base_url)
        calendar = await MarketCalendar.load(fmp)
        prices = PriceService(fmp, settings.poll_interval_s, on_observations=SnapshotWriter(db))
        base_rules = Rules(fill_delay_s=settings.fill_delay_s, fill_timeout_s=settings.fill_timeout_s)
        universe = Universe(fmp, db, base_rules)
        rounds = RoundService(db, calendar)
        engine = FillEngine(db, prices, universe, calendar)
        log.info(
            "services ready (db=%s, holidays=%d)",
            settings.database_url.split("://")[0],
            len(calendar.holidays),
        )
        return cls(settings, db, fmp, calendar, prices, universe, rounds, engine)

    def rules_for(self, rnd) -> Rules:
        return Rules(
            starting_cash=rnd.starting_cash,
            preset=rnd.preset,
            spread_bps=rnd.spread_bps,
            fill_delay_s=self.settings.fill_delay_s,
            fill_timeout_s=self.settings.fill_timeout_s,
        )

    async def close(self) -> None:
        await self.prices.stop()
        await self.fmp.aclose()
        await self.db.dispose()
