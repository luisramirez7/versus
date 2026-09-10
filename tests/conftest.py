from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from versus.db import Database
from versus.engine.fills import FillEngine
from versus.engine.rounds import RoundService
from versus.engine.rules import Rules
from versus.market.clock import MarketCalendar
from versus.market.fmp import Profile
from versus.market.prices import Observation, PriceService
from versus.market.universe import Universe

ET = ZoneInfo("America/New_York")

# A Wednesday during regular hours (not a holiday).
SESSION_NOW = datetime(2026, 9, 16, 14, 0, tzinfo=ET).astimezone(UTC)

PROFILES = {
    "NVDA": Profile(
        "NVDA", "NVIDIA Corporation", "NASDAQ", False, True, Decimal("223.67"), 5_417_511_070_000, 146_342_771
    ),
    "AAPL": Profile(
        "AAPL", "Apple Inc.", "NASDAQ", False, True, Decimal("315.34"), 4_700_000_000_000, 60_000_000
    ),
    "SPY": Profile("SPY", "SPDR S&P 500 ETF Trust", "AMEX", True, True, Decimal("762.40"), None, 40_000_000),
    "NVDX": Profile(
        "NVDX", "T-REX 2X Long NVIDIA Daily Target ETF", "CBOE", True, True, Decimal(40), None, 3_000_000
    ),
    "TINY": Profile("TINY", "Tiny Micro Corp", "NASDAQ", False, True, Decimal("2.10"), 40_000_000, 100_000),
    "THIN": Profile("THIN", "Thinly Traded Inc", "NYSE", False, True, Decimal(50), 900_000_000, 1_000),
}


class FakeFMP:
    """Only what Universe needs; prices are injected through PriceService.ingest."""

    async def profile(self, symbol: str) -> Profile | None:
        return PROFILES.get(symbol)

    async def batch_quotes(self, symbols):
        return {}


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: float) -> datetime:
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(SESSION_NOW)


@pytest.fixture
async def db():
    d = Database("sqlite+aiosqlite:///:memory:")
    await d.create_all()
    yield d
    await d.dispose()


@pytest.fixture
def calendar() -> MarketCalendar:
    return MarketCalendar()


@pytest.fixture
def prices(clock) -> PriceService:
    return PriceService(fmp=None, interval_s=1.0, clock=clock)


@pytest.fixture
def rules() -> Rules:
    return Rules(fill_timeout_s=0.5, fill_delay_s=1.0)


@pytest.fixture
def universe(db, rules) -> Universe:
    return Universe(FakeFMP(), db, rules)


@pytest.fixture
def engine(db, prices, universe, calendar, clock) -> FillEngine:
    return FillEngine(db, prices, universe, calendar, clock=clock)


@pytest.fixture
def rounds(db, calendar, clock) -> RoundService:
    return RoundService(db, calendar, clock=clock)


@pytest.fixture
async def live_round(rounds):
    """A live party with two players, each with $1,000."""
    st = await rounds.create(guild_id=1, channel_id=100, host_user_id=11, cash=1_000, preset="day")
    ana = await rounds.join(st.party.id, 11, "Ana")
    luis = await rounds.join(st.party.id, 22, "Luis")
    st = await rounds.start(st.party.id, 11)
    assert st.party.status == "live"
    return st, ana, luis


def obs(symbol: str, price: str, at: datetime) -> Observation:
    return Observation(symbol, Decimal(price), at)
