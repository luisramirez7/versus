from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from versus.charts.price import CANDLE_MAX, FillMarker, render_comparison, render_price_chart
from versus.charts.ranges import RANGES, fetch_bars, last_session_day, range_label, window
from versus.market.clock import MarketCalendar
from versus.market.fmp import Bar, FMPClient

ET = ZoneInfo("America/New_York")
PNG = b"\x89PNG\r\n\x1a\n"


def make_bars(n: int, *, start: datetime, step: timedelta, seed: int = 1, price: float = 100.0) -> list[Bar]:
    """A seeded random walk. Intraday steps skip the overnight gap so timestamps look like FMP's."""
    rng = random.Random(seed)
    bars = []
    ts = start
    for _ in range(n):
        o = price
        c = o * (1 + rng.uniform(-0.01, 0.01))
        hi = max(o, c) * (1 + rng.uniform(0, 0.004))
        lo = min(o, c) * (1 - rng.uniform(0, 0.004))
        bars.append(
            Bar(
                ts,
                Decimal(f"{o:.2f}"),
                Decimal(f"{hi:.2f}"),
                Decimal(f"{lo:.2f}"),
                Decimal(f"{c:.2f}"),
                rng.randint(10_000, 2_000_000),
            )
        )
        price = c
        ts += step
        if step < timedelta(days=1) and ts.time() >= time(16, 0):
            ts = datetime.combine(ts.date() + timedelta(days=1), time(9, 30), ET)
    return bars


def intraday_bars(n: int, seed: int = 1) -> list[Bar]:
    return make_bars(n, start=datetime(2026, 9, 16, 9, 30, tzinfo=ET), step=timedelta(minutes=5), seed=seed)


def daily_bars(n: int, seed: int = 1) -> list[Bar]:
    return make_bars(n, start=datetime(2026, 1, 2, tzinfo=ET), step=timedelta(days=1), seed=seed)


# ----- rendering -----


def test_candlestick_branch_renders_png():
    bars = intraday_bars(78)
    assert len(bars) <= CANDLE_MAX
    png = render_price_chart(bars, symbol="NVDA", label="Wed Sep 16")
    assert png.startswith(PNG) and len(png) > 10_000


def test_line_branch_renders_png():
    bars = daily_bars(250)
    assert len(bars) > CANDLE_MAX
    png = render_price_chart(bars, symbol="AAPL", label="1 year")
    assert png.startswith(PNG)


@pytest.mark.parametrize("n", [40, 300])
def test_fill_markers_render_on_both_branches(n):
    bars = intraday_bars(n)
    mid = bars[n // 2].ts.astimezone(UTC) + timedelta(seconds=90)
    fills = [
        FillMarker(mid, "buy", bars[n // 2].close),
        FillMarker(bars[-1].ts.astimezone(UTC), "sell", bars[-1].close),
        FillMarker(bars[0].ts - timedelta(days=3), "buy", Decimal(1)),  # outside the window: skipped
    ]
    plain = render_price_chart(bars, symbol="NVDA", label="test")
    marked = render_price_chart(bars, symbol="NVDA", label="test", fills=fills)
    assert marked.startswith(PNG) and marked != plain


def test_single_bar_and_flat_prices_do_not_crash():
    flat = [Bar(datetime(2026, 9, 16, tzinfo=ET), Decimal(5), Decimal(5), Decimal(5), Decimal(5), 0)]
    assert render_price_chart(flat, symbol="X", label="one").startswith(PNG)
    with pytest.raises(ValueError):
        render_price_chart([], symbol="X", label="none")


def test_comparison_three_symbols():
    series = {"NVDA": daily_bars(60, seed=1), "AAPL": daily_bars(60, seed=2), "SPY": daily_bars(58, seed=3)}
    png = render_comparison(series, label="3 months")
    assert png.startswith(PNG)
    with pytest.raises(ValueError):
        render_comparison({"NVDA": daily_bars(5)}, label="x")


# ----- ranges -----


class RecordingFMP:
    def __init__(self, bars: list[Bar] | None = None) -> None:
        self.calls: list[tuple] = []
        self.bars = bars or []

    async def intraday(self, symbol, interval, from_date, to_date):
        self.calls.append(("intraday", symbol, interval, from_date, to_date))
        return self.bars

    async def eod(self, symbol, from_date, to_date):
        self.calls.append(("eod", symbol, from_date, to_date))
        return self.bars


def et(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET).astimezone(UTC)


def test_last_session_day_uses_last_completed_session_when_closed():
    cal = MarketCalendar()
    assert last_session_day(cal, et(2026, 9, 16, 14)) == date(2026, 9, 16)  # open now
    assert last_session_day(cal, et(2026, 9, 16, 18)) == date(2026, 9, 16)  # after today's close
    assert last_session_day(cal, et(2026, 9, 16, 7)) == date(2026, 9, 15)  # before today's open
    assert last_session_day(cal, et(2026, 9, 12, 12)) == date(2026, 9, 11)  # Saturday -> Friday
    assert last_session_day(cal, et(2026, 9, 7, 12)) == date(2026, 9, 4)  # Labor Day -> Friday


def test_windows():
    cal = MarketCalendar()
    now = et(2026, 9, 9, 14)  # Wednesday after Labor Day
    assert window(cal, RANGES["1d"], now) == (date(2026, 9, 9), date(2026, 9, 9))
    # Sessions back from Wed 9: Tue 8, (Mon 7 holiday), Fri 4, Thu 3, Wed 2
    assert window(cal, RANGES["5d"], now) == (date(2026, 9, 2), date(2026, 9, 9))
    assert window(cal, RANGES["1m"], now) == (date(2026, 8, 10), date(2026, 9, 9))
    assert window(cal, RANGES["1y"], now) == (date(2025, 9, 9), date(2026, 9, 9))


async def test_fetch_bars_routes_intraday_and_trims_to_session():
    cal = MarketCalendar()
    day = date(2026, 9, 16)
    inside = make_bars(3, start=datetime(2026, 9, 16, 9, 30, tzinfo=ET), step=timedelta(minutes=5))
    premarket = Bar(datetime(2026, 9, 16, 8, 0, tzinfo=ET), Decimal(1), Decimal(1), Decimal(1), Decimal(1), 1)
    yesterday = Bar(
        datetime(2026, 9, 15, 10, 0, tzinfo=ET), Decimal(1), Decimal(1), Decimal(1), Decimal(1), 1
    )
    fmp = RecordingFMP([premarket, yesterday, *inside])
    bars = await fetch_bars(fmp, cal, "NVDA", "1d", et(2026, 9, 16, 14))
    assert fmp.calls == [("intraday", "NVDA", "5min", day, day)]
    assert bars == inside
    assert range_label("1d", bars) == "Wed Sep 16"

    fmp = RecordingFMP()
    await fetch_bars(fmp, cal, "NVDA", "5d", et(2026, 9, 9, 14))
    assert fmp.calls == [("intraday", "NVDA", "30min", date(2026, 9, 2), date(2026, 9, 9))]


async def test_fetch_bars_routes_daily():
    cal = MarketCalendar()
    fmp = RecordingFMP(daily_bars(3))
    bars = await fetch_bars(fmp, cal, "AAPL", "3m", et(2026, 9, 16, 14))
    assert fmp.calls == [("eod", "AAPL", date(2026, 6, 18), date(2026, 9, 16))]
    assert bars == []  # January bars fall outside the window
    assert range_label("3m", bars) == "3 months"


# ----- FMP parsing (mocked transport, no network) -----


async def test_fmp_bar_parsing_oldest_first_and_new_york_time():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/historical-chart/5min"):
            rows = [
                {
                    "date": "2026-09-08 15:55:00",
                    "open": 224.97,
                    "low": 224.94,
                    "high": 225.9,
                    "close": 225.77,
                    "volume": 4024814,
                },
                {
                    "date": "2026-09-08 09:30:00",
                    "open": 220.0,
                    "low": 219.5,
                    "high": 221.0,
                    "close": 220.5,
                    "volume": 100,
                },
            ]
        else:
            rows = [
                {
                    "symbol": "AAPL",
                    "date": "2026-09-08",
                    "open": 317.1,
                    "high": 320.7,
                    "low": 314.9,
                    "close": 316.22,
                    "volume": 35477100,
                },
                {
                    "symbol": "AAPL",
                    "date": "2026-09-04",
                    "open": 1,
                    "high": 2,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 7,
                },
            ]
        return httpx.Response(200, json=rows)

    fmp = FMPClient(
        "k", "https://example.test/stable", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    intraday = await fmp.intraday("AAPL", "5min", date(2026, 9, 8), date(2026, 9, 8))
    assert [b.ts for b in intraday] == [
        datetime(2026, 9, 8, 9, 30, tzinfo=ET),
        datetime(2026, 9, 8, 15, 55, tzinfo=ET),
    ]
    assert intraday[-1].close == Decimal("225.77") and intraday[-1].volume == 4024814
    assert seen[0].url.params["from"] == "2026-09-08" and seen[0].url.params["symbol"] == "AAPL"

    eod = await fmp.eod("AAPL", date(2026, 9, 1), date(2026, 9, 8))
    assert seen[1].url.path == "/stable/historical-price-eod/full"
    assert [b.ts.date() for b in eod] == [date(2026, 9, 4), date(2026, 9, 8)]
    assert eod[-1].ts == datetime(2026, 9, 8, 0, 0, tzinfo=ET) and eod[-1].high == Decimal("320.7")

    with pytest.raises(ValueError):
        await fmp.intraday("AAPL", "2min", date(2026, 9, 8), date(2026, 9, 8))
    await fmp.aclose()
