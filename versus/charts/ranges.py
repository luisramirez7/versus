"""Chart ranges: which FMP endpoint and interval each range uses, and its date window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from versus.market.clock import ET, MarketCalendar
from versus.market.fmp import Bar, FMPClient


@dataclass(frozen=True)
class RangeSpec:
    interval: str  # an FMP intraday interval, or "1day" for the EOD endpoint
    sessions: int = 0  # intraday: how many session days to cover
    days: int = 0  # daily: how many calendar days back
    label: str = ""

    @property
    def intraday(self) -> bool:
        return self.interval != "1day"


RANGES: dict[str, RangeSpec] = {
    "1d": RangeSpec("5min", sessions=1, label="1 day"),
    "5d": RangeSpec("30min", sessions=5, label="5 days"),
    "1m": RangeSpec("1day", days=30, label="1 month"),
    "3m": RangeSpec("1day", days=90, label="3 months"),
    "6m": RangeSpec("1day", days=180, label="6 months"),
    "1y": RangeSpec("1day", days=365, label="1 year"),
}


def last_session_day(calendar: MarketCalendar, now: datetime) -> date:
    """Today if its session has opened (even if it has since closed), else the last completed session."""
    local = now.astimezone(ET)
    d = local.date()
    s = calendar.session(d)
    if s and local >= s.open:
        return d
    return prev_session_day(calendar, d)


def prev_session_day(calendar: MarketCalendar, d: date) -> date:
    cur = d - timedelta(days=1)
    while not calendar.is_session_day(cur):
        cur -= timedelta(days=1)
    return cur


def window(calendar: MarketCalendar, spec: RangeSpec, now: datetime) -> tuple[date, date]:
    """(from, to) dates for a range as of `now`."""
    if spec.intraday:
        last = last_session_day(calendar, now)
        first = last
        for _ in range(spec.sessions - 1):
            first = prev_session_day(calendar, first)
        return first, last
    today = now.astimezone(ET).date()
    return today - timedelta(days=spec.days), today


def range_label(range_key: str, bars: list[Bar]) -> str:
    """Title fragment: the session date for a one-day chart, the range name otherwise."""
    spec = RANGES[range_key]
    if spec.intraday and spec.sessions == 1 and bars:
        return bars[-1].ts.strftime("%a %b %-d")
    return spec.label


async def fetch_bars(
    fmp: FMPClient, calendar: MarketCalendar, symbol: str, range_key: str, now: datetime
) -> list[Bar]:
    """Bars for `symbol` over the range, oldest first, trimmed to the window and to regular hours."""
    spec = RANGES[range_key]
    first, last = window(calendar, spec, now)
    if spec.intraday:
        bars = await fmp.intraday(symbol, spec.interval, first, last)
        return [b for b in bars if first <= b.ts.date() <= last and _in_session(calendar, b.ts)]
    bars = await fmp.eod(symbol, first, last)
    return [b for b in bars if first <= b.ts.date() <= last]


def _in_session(calendar: MarketCalendar, ts: datetime) -> bool:
    s = calendar.session(ts.date())
    return bool(s and s.open <= ts < s.close)
