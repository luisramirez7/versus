from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from versus.engine.rounds import RoundError
from versus.market.clock import MarketCalendar

ET = ZoneInfo("America/New_York")


def et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_session_days_and_holidays():
    cal = MarketCalendar()
    assert cal.is_session_day(date(2026, 9, 16))
    assert not cal.is_session_day(date(2026, 9, 12))  # Saturday
    assert not cal.is_session_day(date(2026, 9, 7))  # Labor Day
    assert cal.session(date(2026, 11, 27)).close == et(2026, 11, 27, 13)  # half day


def test_is_open_and_next_open():
    cal = MarketCalendar()
    assert cal.is_open(et(2026, 9, 16, 9, 30))
    assert not cal.is_open(et(2026, 9, 16, 16, 0))
    assert not cal.is_open(et(2026, 9, 16, 9, 29))
    # Friday evening → Monday 9:30
    assert cal.next_open(et(2026, 9, 11, 18)) == et(2026, 9, 14, 9, 30).astimezone(UTC)
    # Early morning → same day
    assert cal.next_open(et(2026, 9, 16, 7)) == et(2026, 9, 16, 9, 30).astimezone(UTC)


def test_round_window_counts_sessions_and_skips_holidays():
    cal = MarketCalendar()
    now = et(2026, 9, 3, 12)  # Thursday, live now
    start, end = cal.round_window(now, 5)
    assert start == now.astimezone(UTC)
    # Sessions: Thu 3, Fri 4, (Mon 7 Labor Day skipped) Tue 8, Wed 9, Thu 10
    assert end == et(2026, 9, 10, 16).astimezone(UTC)

    start, end = cal.round_window(et(2026, 9, 5, 12), 1)  # Saturday → Tuesday sprint
    assert start == et(2026, 9, 8, 9, 30).astimezone(UTC)
    assert end == et(2026, 9, 8, 16).astimezone(UTC)


async def test_party_lifecycle(rounds, clock):
    st = await rounds.create(guild_id=1, channel_id=1, host_user_id=10, cash=1_000, preset="week")
    assert st.party.status == "lobby"
    with pytest.raises(RoundError, match="already has a party"):
        await rounds.create(guild_id=1, channel_id=1, host_user_id=10, cash=1_000, preset="day")
    with pytest.raises(RoundError, match="at least 2"):
        await rounds.start(st.party.id, 10)
    await rounds.join(st.party.id, 10, "Host")
    await rounds.join(st.party.id, 20, "Guest")
    with pytest.raises(RoundError, match="already in"):
        await rounds.join(st.party.id, 20, "Guest")
    with pytest.raises(RoundError, match="Only the host"):
        await rounds.start(st.party.id, 20)
    st = await rounds.start(st.party.id, 10)
    assert st.party.status == "live" and st.round.end_at is not None
    with pytest.raises(RoundError, match="already live"):
        await rounds.join(st.party.id, 30, "Late")
    st = await rounds.end_now(st.party.id, 10)
    _, to_settle = await rounds.due(clock.now)
    assert [r.id for r in to_settle] == [st.round.id]


async def test_scheduled_when_market_closed(rounds, clock):
    clock.now = et(2026, 9, 12, 12).astimezone(UTC)  # Saturday
    st = await rounds.create(guild_id=1, channel_id=2, host_user_id=10, cash=10_000, preset="day")
    await rounds.join(st.party.id, 10, "A")
    await rounds.join(st.party.id, 20, "B")
    st = await rounds.start(st.party.id, 10)
    assert st.party.status == "scheduled"
    assert st.round.start_at == et(2026, 9, 14, 9, 30).astimezone(UTC)
    to_live, _ = await rounds.due(et(2026, 9, 14, 9, 31).astimezone(UTC))
    assert [r.id for r in to_live] == [st.round.id]
    assert await rounds.mark_live(st.round.id)
    assert not await rounds.mark_live(st.round.id)
