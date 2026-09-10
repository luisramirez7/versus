"""End of round: freeze prices, rank, write the settlement record. Idempotent."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from versus.db import Database, Fill, LeaderboardSnapshot, Party, Portfolio, Round
from versus.market.prices import PriceService

from .valuation import Standing, build_price_lookup, held_symbols, standings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeHighlight:
    display_name: str
    symbol: str
    pnl: Decimal
    pct: Decimal


@dataclass(frozen=True)
class Settlement:
    round_id: int
    ended_at: datetime
    standings: list[Standing]
    best_trade: TradeHighlight | None
    worst_trade: TradeHighlight | None
    most_active: tuple[str, int] | None
    prices_used: dict[str, str]
    already_settled: bool = False


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(type(o))


async def settle_round(
    db: Database, prices: PriceService | None, round_id: int, now: datetime | None = None
) -> Settlement | None:
    now = now or datetime.now(UTC)
    async with db.session() as s:
        rnd = await s.get(Round, round_id)
        if rnd is None:
            return None
        if rnd.status == "settled":
            return _from_json(rnd)
        rnd.status = "settling"
        await s.commit()
        end_at = rnd.end_at or now

    symbols = await held_symbols(db, [round_id])
    # Prices at or before the end instant, from stored snapshots; fall back to whatever is live.
    lookup_stored = await build_price_lookup(db, None, symbols, at_or_before=end_at)
    lookup_live = await build_price_lookup(db, prices, symbols, None)

    def lookup(symbol: str) -> Decimal | None:
        return lookup_stored(symbol) or lookup_live(symbol)

    final = await standings(db, lookup, round_id)
    prices_used = {sym: str(p) for sym in symbols if (p := lookup(sym)) is not None}

    async with db.session() as s:
        names = {
            p.id: p.display_name
            for p in (await s.execute(select(Portfolio).where(Portfolio.round_id == round_id)))
            .scalars()
            .all()
        }
        fills = (
            (
                await s.execute(
                    select(Fill)
                    .join(Portfolio, Portfolio.id == Fill.portfolio_id)
                    .where(Portfolio.round_id == round_id)
                    .options(selectinload(Fill.order))
                )
            )
            .scalars()
            .all()
        )
    sells = [f for f in fills if f.side == "sell" and f.realized_pnl is not None]
    best = worst = None
    if sells:
        b = max(sells, key=lambda f: f.realized_pnl)
        w = min(sells, key=lambda f: f.realized_pnl)
        best = _highlight(b, names)
        worst = _highlight(w, names) if w is not b else None
    most_active = None
    if final:
        top = max(final, key=lambda st: st.fills)
        if top.fills > 0:
            most_active = (top.display_name, top.fills)

    settlement = Settlement(round_id, end_at, final, best, worst, most_active, prices_used)

    async with db.session() as s:
        rnd = await s.get(Round, round_id)
        rnd.status = "settled"
        rnd.settled_at = now
        rnd.settlement_json = json.dumps(asdict(settlement), default=_json_default)
        party = await s.get(Party, rnd.party_id)
        if party is not None:
            party.status = "settled"
        for st in final:
            s.add(
                LeaderboardSnapshot(
                    round_id=round_id,
                    portfolio_id=st.portfolio_id,
                    ts=end_at,
                    equity=st.equity,
                    cash=st.cash,
                    rank=st.rank,
                )
            )
        await s.commit()
    log.info("round %s settled: %s", round_id, [(st.display_name, str(st.equity)) for st in final])
    return settlement


def _highlight(f: Fill, names: dict[int, str]) -> TradeHighlight:
    cost = f.notional - f.realized_pnl
    pct = (f.realized_pnl / cost) if cost else Decimal(0)
    return TradeHighlight(names.get(f.portfolio_id, "?"), f.symbol, f.realized_pnl, pct)


def _from_json(rnd: Round) -> Settlement:
    d = json.loads(rnd.settlement_json or "{}")
    sts = [
        Standing(
            **{
                **st,
                "equity": Decimal(st["equity"]),
                "cash": Decimal(st["cash"]),
                "return_pct": Decimal(st["return_pct"]),
                "top_weight": Decimal(st["top_weight"]) if st.get("top_weight") is not None else None,
                "joined_at": datetime.fromisoformat(st["joined_at"]),
            }
        )
        for st in d.get("standings", [])
    ]

    def hl(x):
        return (
            TradeHighlight(x["display_name"], x["symbol"], Decimal(x["pnl"]), Decimal(x["pct"]))
            if x
            else None
        )

    return Settlement(
        round_id=rnd.id,
        ended_at=datetime.fromisoformat(d["ended_at"]) if d.get("ended_at") else rnd.settled_at,
        standings=sts,
        best_trade=hl(d.get("best_trade")),
        worst_trade=hl(d.get("worst_trade")),
        most_active=tuple(d["most_active"]) if d.get("most_active") else None,
        prices_used=d.get("prices_used", {}),
        already_settled=True,
    )
