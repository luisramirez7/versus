"""Equity, standings, and the portfolio view. Reads only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from versus.db import Database, Fill, Portfolio, Round
from versus.market.prices import PriceService

from .snapshots import latest_snapshot_prices

CENT4 = Decimal("0.0001")
PriceLookup = Callable[[str], Decimal | None]


@dataclass(frozen=True)
class Holding:
    symbol: str
    qty: Decimal
    avg_cost: Decimal
    last: Decimal
    market_value: Decimal
    unrealized: Decimal
    unrealized_pct: Decimal
    weight: Decimal
    stale: bool  # no live or stored price; valued at cost


@dataclass(frozen=True)
class PortfolioView:
    portfolio_id: int
    owner_id: int
    display_name: str
    cash: Decimal
    equity: Decimal
    starting_cash: Decimal
    return_pct: Decimal
    holdings: list[Holding]
    fills: int
    rank: int | None


@dataclass(frozen=True)
class Standing:
    rank: int
    portfolio_id: int
    owner_id: int
    display_name: str
    equity: Decimal
    cash: Decimal
    return_pct: Decimal
    fills: int
    top_holding: str | None
    top_weight: Decimal | None
    joined_at: datetime


async def build_price_lookup(
    db: Database, prices: PriceService | None, symbols: list[str], at_or_before: datetime | None = None
) -> PriceLookup:
    """Live price if we have one (and no cutoff), else the latest stored snapshot."""
    stored = await latest_snapshot_prices(db, symbols, at_or_before)

    def lookup(symbol: str) -> Decimal | None:
        if at_or_before is None and prices is not None:
            obs = prices.latest(symbol)
            if obs is not None:
                return obs.price
        return stored.get(symbol)

    return lookup


async def _fill_counts(s, portfolio_ids: list[int]) -> dict[int, int]:
    if not portfolio_ids:
        return {}
    rows = (
        await s.execute(
            select(Fill.portfolio_id, func.count(Fill.id))
            .where(Fill.portfolio_id.in_(portfolio_ids))
            .group_by(Fill.portfolio_id)
        )
    ).all()
    return {pid: n for pid, n in rows}


def _value(pf: Portfolio, lookup: PriceLookup) -> tuple[Decimal, list[Holding]]:
    holdings: list[Holding] = []
    total_positions = Decimal(0)
    for pos in pf.positions:
        last = lookup(pos.symbol)
        stale = last is None
        last = pos.avg_cost if last is None else last
        mv = (pos.qty * last).quantize(CENT4, ROUND_HALF_EVEN)
        cost = (pos.qty * pos.avg_cost).quantize(CENT4, ROUND_HALF_EVEN)
        unreal = mv - cost
        holdings.append(
            Holding(
                symbol=pos.symbol,
                qty=pos.qty,
                avg_cost=pos.avg_cost,
                last=last,
                market_value=mv,
                unrealized=unreal,
                unrealized_pct=(unreal / cost) if cost else Decimal(0),
                weight=Decimal(0),
                stale=stale,
            )
        )
        total_positions += mv
    equity = pf.cash + total_positions
    if equity > 0:
        holdings = [
            Holding(**{**h.__dict__, "weight": (h.market_value / equity).quantize(Decimal("0.0001"))})
            for h in holdings
        ]
    holdings.sort(key=lambda h: h.market_value, reverse=True)
    return equity, holdings


async def standings(db: Database, lookup: PriceLookup, round_id: int) -> list[Standing]:
    async with db.session() as s:
        rnd = await s.get(Round, round_id)
        if rnd is None:
            return []
        pfs = (
            (
                await s.execute(
                    select(Portfolio)
                    .where(Portfolio.round_id == round_id)
                    .options(selectinload(Portfolio.positions))
                )
            )
            .scalars()
            .all()
        )
        counts = await _fill_counts(s, [p.id for p in pfs])
    rows = []
    for pf in pfs:
        equity, holdings = _value(pf, lookup)
        top = holdings[0] if holdings else None
        rows.append(
            (
                pf,
                equity,
                counts.get(pf.id, 0),
                top.symbol if top else None,
                top.weight if top else None,
            )
        )
    # equity desc, fewer fills, earlier join
    rows.sort(key=lambda r: (-r[1], r[2], r[0].joined_at))
    out: list[Standing] = []
    for i, (pf, equity, n, top_sym, top_w) in enumerate(rows, start=1):
        out.append(
            Standing(
                rank=i,
                portfolio_id=pf.id,
                owner_id=pf.owner_id,
                display_name=pf.display_name,
                equity=equity,
                cash=pf.cash,
                return_pct=(equity / rnd.starting_cash - 1) if rnd.starting_cash else Decimal(0),
                fills=n,
                top_holding=top_sym,
                top_weight=top_w,
                joined_at=pf.joined_at,
            )
        )
    return out


async def portfolio_view(db: Database, lookup: PriceLookup, portfolio_id: int) -> PortfolioView | None:
    async with db.session() as s:
        pf = (
            await s.execute(
                select(Portfolio)
                .where(Portfolio.id == portfolio_id)
                .options(selectinload(Portfolio.positions))
            )
        ).scalar_one_or_none()
        if pf is None:
            return None
        rnd = await s.get(Round, pf.round_id)
        counts = await _fill_counts(s, [pf.id])
    equity, holdings = _value(pf, lookup)
    rank = None
    for st in await standings(db, lookup, pf.round_id):
        if st.portfolio_id == pf.id:
            rank = st.rank
    return PortfolioView(
        portfolio_id=pf.id,
        owner_id=pf.owner_id,
        display_name=pf.display_name,
        cash=pf.cash,
        equity=equity,
        starting_cash=rnd.starting_cash,
        return_pct=(equity / rnd.starting_cash - 1) if rnd.starting_cash else Decimal(0),
        holdings=holdings,
        fills=counts.get(pf.id, 0),
        rank=rank,
    )


async def held_symbols(db: Database, round_ids: list[int] | None = None) -> list[str]:
    """Every symbol held in the given rounds (or all live rounds)."""
    from versus.db import Position  # local import keeps module import order simple

    async with db.session() as s:
        q = select(Position.symbol).distinct().join(Portfolio, Portfolio.id == Position.portfolio_id)
        if round_ids is None:
            q = q.join(Round, Round.id == Portfolio.round_id).where(Round.status == "live")
        else:
            q = q.where(Portfolio.round_id.in_(round_ids))
        return [r[0] for r in (await s.execute(q)).all()]
