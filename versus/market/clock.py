"""NYSE session calendar: regular hours, holidays, half days, and "N sessions from here"."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .fmp import FMPClient, Holiday

ET = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)

# Fallback if FMP is unreachable. NYSE 2026 full closures and early closes.
FALLBACK_HOLIDAYS_2026: dict[date, Holiday] = {
    d: Holiday(d, n, True, None)
    for d, n in [
        (date(2026, 1, 1), "New Year's Day"),
        (date(2026, 1, 19), "Martin Luther King Jr. Day"),
        (date(2026, 2, 16), "Presidents' Day"),
        (date(2026, 4, 3), "Good Friday"),
        (date(2026, 5, 25), "Memorial Day"),
        (date(2026, 6, 19), "Juneteenth"),
        (date(2026, 7, 3), "Independence Day (observed)"),
        (date(2026, 9, 7), "Labor Day"),
        (date(2026, 11, 26), "Thanksgiving"),
        (date(2026, 12, 25), "Christmas"),
    ]
} | {
    date(2026, 11, 27): Holiday(date(2026, 11, 27), "Day after Thanksgiving", False, "13:00"),
    date(2026, 12, 24): Holiday(date(2026, 12, 24), "Christmas Eve", False, "13:00"),
}


@dataclass(frozen=True)
class Session:
    day: date
    open: datetime  # aware, ET
    close: datetime


class MarketCalendar:
    def __init__(self, holidays: dict[date, Holiday] | None = None) -> None:
        self.holidays = dict(holidays or FALLBACK_HOLIDAYS_2026)

    @classmethod
    async def load(cls, fmp: FMPClient, today: date | None = None) -> MarketCalendar:
        today = today or datetime.now(ET).date()
        try:
            rows = await fmp.holidays("NYSE", today - timedelta(days=30), today + timedelta(days=400))
        except Exception:  # noqa: BLE001
            return cls()
        holidays = {h.date: h for h in rows if h.is_closed or h.adj_close_time}
        return cls(holidays or None)

    # ----- days -----
    def is_session_day(self, d: date) -> bool:
        if d.weekday() >= 5:
            return False
        h = self.holidays.get(d)
        return not (h and h.is_closed)

    def session(self, d: date) -> Session | None:
        if not self.is_session_day(d):
            return None
        close = CLOSE
        h = self.holidays.get(d)
        if h and h.adj_close_time:
            hh, mm = h.adj_close_time.split(":")
            close = time(int(hh), int(mm))
        return Session(d, datetime.combine(d, OPEN, ET), datetime.combine(d, close, ET))

    def next_session_day(self, d: date, inclusive: bool = False) -> date:
        cur = d if inclusive else d + timedelta(days=1)
        while not self.is_session_day(cur):
            cur += timedelta(days=1)
        return cur

    # ----- instants -----
    def is_open(self, now: datetime) -> bool:
        local = now.astimezone(ET)
        s = self.session(local.date())
        return bool(s and s.open <= local < s.close)

    def current_session(self, now: datetime) -> Session | None:
        local = now.astimezone(ET)
        s = self.session(local.date())
        return s if s and s.open <= local < s.close else None

    def next_open(self, now: datetime) -> datetime:
        """The next opening bell strictly after `now` (or now's own bell if before it today)."""
        local = now.astimezone(ET)
        s = self.session(local.date())
        if s and local < s.open:
            return s.open.astimezone(UTC)
        d = self.next_session_day(local.date())
        return self.session(d).open.astimezone(UTC)  # type: ignore[union-attr]

    def round_window(self, now: datetime, sessions: int) -> tuple[datetime, datetime]:
        """(start_at, end_at) for a round of N sessions starting now (if open) or at the next open."""
        if self.is_open(now):
            start = now.astimezone(UTC)
            first_day = now.astimezone(ET).date()
        else:
            start = self.next_open(now)
            first_day = start.astimezone(ET).date()
        day = first_day
        for _ in range(sessions - 1):
            day = self.next_session_day(day)
        end = self.session(day).close.astimezone(UTC)  # type: ignore[union-attr]
        return start, end

    def last_close_before(self, now: datetime) -> datetime:
        local = now.astimezone(ET)
        d = local.date()
        while True:
            s = self.session(d)
            if s and s.close <= local:
                return s.close.astimezone(UTC)
            d -= timedelta(days=1)
