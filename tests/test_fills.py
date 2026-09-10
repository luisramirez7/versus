from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

import pytest

from versus.db import Fill, Order, Position
from versus.engine.fills import OrderError

from .conftest import obs


async def run_order(engine, prices, clock, *, fresh_price: str, delay_s: float = 2.0, **kw):
    """Submit an order, then publish a fresh quote `delay_s` later so the fill can happen."""
    task = asyncio.create_task(engine.market_order(**kw))
    await asyncio.sleep(0.05)
    at = clock.tick(delay_s)
    prices.ingest([obs(kw["symbol"], fresh_price, at)])
    return await task


async def test_buy_dollars_fills_at_next_fresh_price_with_spread(engine, prices, clock, live_round, rules):
    st, ana, _ = live_round
    # A stale price from before the order must NOT be used.
    prices.ingest([obs("NVDA", "200.00", clock.now - timedelta(seconds=30))])
    r = await run_order(
        engine,
        prices,
        clock,
        fresh_price="223.67",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="NVDA",
        side="buy",
        dollars=Decimal(500),
        rules=rules,
    )
    assert r.quote_price == Decimal("223.67")
    assert r.price == Decimal("223.7259")  # +2.5 bp
    assert r.qty == (Decimal(500) / r.price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    assert r.notional <= Decimal(500)
    assert r.cash_after == Decimal(1000) - r.notional
    assert r.fill_delay_s >= rules.fill_delay_s


async def test_sell_all_realizes_pnl_and_closes_position(engine, prices, clock, live_round, rules, db):
    st, ana, _ = live_round
    await run_order(
        engine,
        prices,
        clock,
        fresh_price="100.00",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="AAPL",
        side="buy",
        shares=Decimal(5),
        rules=rules,
    )
    r = await run_order(
        engine,
        prices,
        clock,
        fresh_price="110.00",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="AAPL",
        side="sell",
        sell_all=True,
        rules=rules,
    )
    assert r.closed_position
    assert r.qty == Decimal(5)
    assert r.realized_pnl > Decimal(49)  # ~ +$50 minus spread
    assert r.realized_pct > Decimal("0.09")
    async with db.session() as s:
        assert await s.get(Position, (ana.id, "AAPL")) is None
        fills = (await s.execute(Fill.__table__.select())).all()
        assert len(fills) == 2


async def test_average_cost_updates_on_second_buy(engine, prices, clock, live_round, rules, db):
    st, ana, _ = live_round
    await run_order(
        engine,
        prices,
        clock,
        fresh_price="100.00",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="AAPL",
        side="buy",
        shares=Decimal(1),
        rules=rules,
    )
    await run_order(
        engine,
        prices,
        clock,
        fresh_price="200.00",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="AAPL",
        side="buy",
        shares=Decimal(1),
        rules=rules,
    )
    async with db.session() as s:
        pos = await s.get(Position, (ana.id, "AAPL"))
    assert pos.qty == Decimal(2)
    assert Decimal("150.03") < pos.avg_cost < Decimal("150.04")  # (100.025 + 200.05) / 2


async def test_rejects_when_cash_is_short(engine, prices, clock, live_round, rules, db):
    st, ana, _ = live_round
    with pytest.raises(OrderError, match="Not enough cash"):
        await engine.market_order(
            round_id=st.round.id,
            portfolio_id=ana.id,
            symbol="NVDA",
            side="buy",
            dollars=Decimal(5000),
            rules=rules,
        )
    async with db.session() as s:
        orders = (await s.execute(Order.__table__.select())).all()
    assert orders and orders[0].status == "rejected"


async def test_rejects_ineligible_symbols(engine, prices, clock, live_round, rules):
    st, ana, _ = live_round
    for sym, why in [
        ("NVDX", "leveraged"),
        ("TINY", "price below"),
        ("THIN", "average volume"),
        ("ZZZZ", "unknown"),
    ]:
        with pytest.raises(OrderError, match=why):
            await engine.market_order(
                round_id=st.round.id,
                portfolio_id=ana.id,
                symbol=sym,
                side="buy",
                dollars=Decimal(10),
                rules=rules,
            )


async def test_etf_without_market_cap_is_eligible(engine, prices, clock, live_round, rules):
    st, ana, _ = live_round
    r = await run_order(
        engine,
        prices,
        clock,
        fresh_price="762.40",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="SPY",
        side="buy",
        dollars=Decimal(100),
        rules=rules,
    )
    assert r.qty > 0


async def test_rejects_when_market_closed(engine, prices, clock, live_round, rules):
    st, ana, _ = live_round
    clock.tick(6 * 3600)  # 8 PM ET
    with pytest.raises(OrderError, match="Market is closed"):
        await engine.market_order(
            round_id=st.round.id,
            portfolio_id=ana.id,
            symbol="NVDA",
            side="buy",
            dollars=Decimal(10),
            rules=rules,
        )


async def test_rejects_selling_more_than_held(engine, prices, clock, live_round, rules):
    st, ana, _ = live_round
    with pytest.raises(OrderError, match="don't hold"):
        await engine.market_order(
            round_id=st.round.id,
            portfolio_id=ana.id,
            symbol="NVDA",
            side="sell",
            shares=Decimal(1),
            rules=rules,
        )
    await run_order(
        engine,
        prices,
        clock,
        fresh_price="100.00",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="AAPL",
        side="buy",
        shares=Decimal(2),
        rules=rules,
    )
    with pytest.raises(OrderError, match="You hold 2"):
        await engine.market_order(
            round_id=st.round.id,
            portfolio_id=ana.id,
            symbol="AAPL",
            side="sell",
            shares=Decimal(3),
            rules=rules,
        )


async def test_times_out_without_a_fresh_price(engine, prices, clock, live_round, rules):
    st, ana, _ = live_round
    prices.ingest([obs("NVDA", "223.67", clock.now)])  # observed at receipt, not ≥ 1 s after
    with pytest.raises(OrderError, match="No fresh price"):
        await engine.market_order(
            round_id=st.round.id,
            portfolio_id=ana.id,
            symbol="NVDA",
            side="buy",
            dollars=Decimal(10),
            rules=rules,
        )


async def test_duplicate_interaction_is_refused(engine, prices, clock, live_round, rules):
    st, ana, _ = live_round
    await run_order(
        engine,
        prices,
        clock,
        fresh_price="100.00",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="AAPL",
        side="buy",
        shares=Decimal(1),
        rules=rules,
        interaction_id=999,
    )
    with pytest.raises(OrderError, match="already processed"):
        await engine.market_order(
            round_id=st.round.id,
            portfolio_id=ana.id,
            symbol="AAPL",
            side="buy",
            shares=Decimal(1),
            rules=rules,
            interaction_id=999,
        )


async def test_adv_cap(engine, prices, clock, live_round, rules, rounds):
    # THIN has avg volume 1,000 but is ineligible; use NVDA with a huge order on a $100k party instead.
    st2 = await rounds.create(guild_id=1, channel_id=200, host_user_id=1, cash=100_000, preset="day")
    a = await rounds.join(st2.party.id, 1, "A")
    await rounds.join(st2.party.id, 2, "B")
    st2 = await rounds.start(st2.party.id, 1)
    big = rules.__class__(**{**rules.__dict__, "adv_cap_pct": Decimal("0.0000001")})
    with pytest.raises(OrderError, match="average daily volume"):
        await run_order(
            engine,
            prices,
            clock,
            fresh_price="223.67",
            round_id=st2.round.id,
            portfolio_id=a.id,
            symbol="NVDA",
            side="buy",
            dollars=Decimal(50_000),
            rules=big,
        )
