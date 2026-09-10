from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from versus.db import PriceSnapshot, Round
from versus.engine.settlement import settle_round
from versus.engine.snapshots import SnapshotWriter
from versus.engine.valuation import build_price_lookup, portfolio_view, standings

from .conftest import obs
from .test_fills import run_order


async def test_standings_order_and_tiebreak(engine, prices, clock, live_round, rules, db):
    st, ana, luis = live_round
    # Ana buys AAPL at 100, Luis buys NVDA at 200. Then AAPL rallies.
    await run_order(
        engine,
        prices,
        clock,
        fresh_price="100.00",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="AAPL",
        side="buy",
        dollars=Decimal(500),
        rules=rules,
    )
    await run_order(
        engine,
        prices,
        clock,
        fresh_price="200.00",
        round_id=st.round.id,
        portfolio_id=luis.id,
        symbol="NVDA",
        side="buy",
        dollars=Decimal(500),
        rules=rules,
    )
    prices.ingest([obs("AAPL", "120.00", clock.tick(5)), obs("NVDA", "200.00", clock.now)])
    lookup = await build_price_lookup(db, prices, ["AAPL", "NVDA"])
    board = await standings(db, lookup, st.round.id)
    assert [s.display_name for s in board] == ["Ana", "Luis"]
    assert board[0].rank == 1 and board[0].return_pct > Decimal("0.09")
    assert board[1].return_pct < 0  # spread cost
    assert board[0].top_holding == "AAPL"

    view = await portfolio_view(db, lookup, ana.id)
    assert view.rank == 1
    assert view.holdings[0].symbol == "AAPL"
    assert view.holdings[0].unrealized > 0
    assert view.equity == view.cash + view.holdings[0].market_value


async def test_tiebreak_prefers_fewer_fills_then_earlier_join(rounds, db, prices):
    st = await rounds.create(guild_id=1, channel_id=300, host_user_id=1, cash=1_000, preset="day")
    await rounds.join(st.party.id, 1, "First")
    await rounds.join(st.party.id, 2, "Second")
    st = await rounds.start(st.party.id, 1)
    lookup = await build_price_lookup(db, prices, [])
    board = await standings(db, lookup, st.round.id)
    assert [s.display_name for s in board] == ["First", "Second"]
    assert all(s.equity == Decimal(1000) for s in board)


async def test_settlement_uses_prices_at_or_before_end_and_is_idempotent(
    engine, prices, clock, live_round, rules, db
):
    st, ana, _luis = live_round
    writer = SnapshotWriter(db, min_gap=timedelta(seconds=0))
    await run_order(
        engine,
        prices,
        clock,
        fresh_price="100.00",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="AAPL",
        side="buy",
        shares=Decimal(4),
        rules=rules,
    )
    # Ana takes a profit on half; that becomes the best trade.
    await run_order(
        engine,
        prices,
        clock,
        fresh_price="150.00",
        round_id=st.round.id,
        portfolio_id=ana.id,
        symbol="AAPL",
        side="sell",
        shares=Decimal(2),
        rules=rules,
    )
    # Snapshot at the end instant, then a later (post-close) price that must be ignored.
    end_at = clock.tick(60)
    await writer([obs("AAPL", "140.00", end_at)])
    await writer([obs("AAPL", "999.00", end_at + timedelta(minutes=5))])
    async with db.session() as s:
        (await s.get(Round, st.round.id)).end_at = end_at
        await s.commit()

    result = await settle_round(db, prices, st.round.id, now=end_at + timedelta(minutes=10))
    assert result is not None and not result.already_settled
    assert result.prices_used["AAPL"] == "140.0000"
    assert result.standings[0].display_name == "Ana"
    assert result.best_trade and result.best_trade.symbol == "AAPL" and result.best_trade.pnl > 99
    assert result.most_active == ("Ana", 2)

    again = await settle_round(db, prices, st.round.id)
    assert again.already_settled
    assert [s.display_name for s in again.standings] == [s.display_name for s in result.standings]
    assert again.standings[0].equity == result.standings[0].equity

    async with db.session() as s:
        rnd = await s.get(Round, st.round.id)
        assert rnd.status == "settled"
        snaps = (await s.execute(PriceSnapshot.__table__.select())).all()
        assert len(snaps) == 2
