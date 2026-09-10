"""The fill engine: the only thing that changes cash and positions.

Rule: a market order fills at the first price observed at least `fill_delay_s` after the
server received it, buys at quote + half spread, sells at quote − half spread. Everything
runs inside one transaction under a per-portfolio lock.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from versus.db import Database, Fill, Order, Portfolio, Position, Round
from versus.market.prices import PriceService

from .rules import Rules

if TYPE_CHECKING:
    from versus.market.clock import MarketCalendar
    from versus.market.universe import Universe

log = logging.getLogger(__name__)

CENT4 = Decimal("0.0001")
SHARE6 = Decimal("0.000001")


class OrderError(Exception):
    """User-facing rejection. The message is shown to the player as-is."""


@dataclass(frozen=True)
class FillResult:
    symbol: str
    side: str
    qty: Decimal
    price: Decimal
    quote_price: Decimal
    notional: Decimal
    cash_after: Decimal
    position_qty_after: Decimal
    avg_cost_after: Decimal | None
    realized_pnl: Decimal | None
    realized_pct: Decimal | None
    received_at: datetime
    quoted_at: datetime
    filled_at: datetime
    closed_position: bool

    @property
    def fill_delay_s(self) -> float:
        return (self.quoted_at - self.received_at).total_seconds()


class FillEngine:
    def __init__(
        self,
        db: Database,
        prices: PriceService,
        universe: Universe,
        calendar: MarketCalendar,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.db = db
        self.prices = prices
        self.universe = universe
        self.calendar = calendar
        self.clock = clock
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def market_order(
        self,
        *,
        round_id: int,
        portfolio_id: int,
        symbol: str,
        side: str,
        rules: Rules,
        shares: Decimal | None = None,
        dollars: Decimal | None = None,
        sell_all: bool = False,
        interaction_id: int | None = None,
    ) -> FillResult:
        received_at = self.clock()
        symbol = symbol.upper().strip()
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")

        async def reject(reason: str) -> OrderError:
            await self._record_rejection(
                portfolio_id, symbol, side, shares, dollars, interaction_id, received_at, reason
            )
            return OrderError(reason)

        # ----- request shape -----
        if sell_all:
            if side != "sell":
                raise ValueError("sell_all only applies to sells")
            shares, dollars = None, None
        elif (shares is None) == (dollars is None):
            raise await reject("Give either `shares` or `dollars`, not both.")
        elif (shares is not None and shares <= 0) or (dollars is not None and dollars <= 0):
            raise await reject("The amount has to be positive.")

        # ----- state checks -----
        async with self.db.session() as s:
            if interaction_id is not None:
                dup = (
                    await s.execute(select(Order.id).where(Order.interaction_id == interaction_id))
                ).first()
                if dup:
                    raise OrderError("That order was already processed.")
            rnd = await s.get(Round, round_id)
            if rnd is None or rnd.status != "live":
                raise await reject("The round isn't live right now.")
            pf = await s.get(Portfolio, portfolio_id)
            if pf is None or pf.round_id != round_id:
                raise await reject("You're not in this round. Use `/join` before it starts.")
            pos = await s.get(Position, (portfolio_id, symbol))

        if not self.calendar.is_open(received_at):
            nxt = self.calendar.next_open(received_at)
            raise await reject(
                f"Market is closed. Opens <t:{int(nxt.timestamp())}:f>. After-hours orders come in v1.1."
            )

        avg_volume: int | None = None
        if side == "buy":
            elig = await self.universe.check(symbol, received_at)
            if not elig.eligible:
                raise await reject(f"{symbol} isn't tradeable in this round: {elig.reason}.")
            avg_volume = elig.avg_volume
            if dollars is not None and dollars > pf.cash:
                raise await reject(
                    f"Not enough cash: ${pf.cash:,.2f} available, order needs ${dollars:,.2f}."
                )
        else:
            if pos is None or pos.qty <= 0:
                raise await reject(f"You don't hold any {symbol}.")
            if shares is not None and shares > pos.qty:
                raise await reject(f"You hold {pos.qty.normalize():f} {symbol}, not {shares.normalize():f}.")

        # ----- wait for a fresh price -----
        after = received_at + timedelta(seconds=rules.fill_delay_s)
        try:
            obs = await self.prices.wait_for_fresh(symbol, after, timeout=rules.fill_timeout_s)
        except TimeoutError:
            raise await reject(
                f"No fresh price for {symbol} arrived within {int(rules.fill_timeout_s)} s. Try again."
            )

        hs = rules.half_spread()
        exec_price = (obs.price * (1 + hs if side == "buy" else 1 - hs)).quantize(CENT4, ROUND_HALF_EVEN)

        # ----- the transaction -----
        async with self._locks[portfolio_id], self.db.session() as s:
            pf = await s.get(Portfolio, portfolio_id)
            pos = await s.get(Position, (portfolio_id, symbol))
            filled_at = self.clock()
            realized = realized_pct = None
            closed = False

            if side == "buy":
                if dollars is not None:
                    spend = min(dollars, pf.cash)
                    qty = (spend / exec_price).quantize(SHARE6, ROUND_DOWN)
                else:
                    qty = shares.quantize(SHARE6, ROUND_DOWN)  # type: ignore[union-attr]
                notional = (qty * exec_price).quantize(CENT4, ROUND_HALF_EVEN)
                if qty <= 0 or notional < rules.min_notional:
                    raise await reject(f"Order is below the ${rules.min_notional} minimum.")
                if notional > pf.cash:
                    raise await reject(
                        f"Not enough cash: ${pf.cash:,.2f} available, order needs ${notional:,.2f}."
                    )
                if avg_volume:
                    cap = (Decimal(avg_volume) * rules.adv_cap_pct).quantize(SHARE6, ROUND_DOWN)
                    if qty > cap:
                        raise await reject(
                            f"Order exceeds {rules.adv_cap_pct:.0%} of {symbol}'s average daily volume "
                            f"(max {cap.normalize():f} shares)."
                        )
                pf.cash = pf.cash - notional
                if pos is None:
                    pos = Position(portfolio_id=portfolio_id, symbol=symbol, qty=qty, avg_cost=exec_price)
                    s.add(pos)
                else:
                    total_cost = pos.qty * pos.avg_cost + notional
                    pos.qty = pos.qty + qty
                    pos.avg_cost = (total_cost / pos.qty).quantize(CENT4, ROUND_HALF_EVEN)
                qty_after, avg_after = pos.qty, pos.avg_cost
            else:
                held = pos.qty if pos else Decimal(0)
                if sell_all:
                    qty = held
                elif shares is not None:
                    qty = shares.quantize(SHARE6, ROUND_DOWN)
                else:
                    qty = min(held, (dollars / exec_price).quantize(SHARE6, ROUND_DOWN))  # type: ignore[operator]
                if qty <= 0 or qty > held:
                    raise await reject(f"You hold {held.normalize():f} {symbol}.")
                notional = (qty * exec_price).quantize(CENT4, ROUND_HALF_EVEN)
                cost_basis = (qty * pos.avg_cost).quantize(CENT4, ROUND_HALF_EVEN)  # type: ignore[union-attr]
                realized = notional - cost_basis
                realized_pct = (realized / cost_basis) if cost_basis else Decimal(0)
                pf.cash = pf.cash + notional
                pos.qty = pos.qty - qty  # type: ignore[union-attr]
                if pos.qty <= SHARE6:  # dust → close
                    closed = True
                    qty_after, avg_after = Decimal(0), None
                    await s.delete(pos)
                else:
                    qty_after, avg_after = pos.qty, pos.avg_cost

            order = Order(
                portfolio_id=portfolio_id,
                symbol=symbol,
                side=side,
                req_shares=shares,
                req_dollars=dollars,
                status="filled",
                interaction_id=interaction_id,
                received_at=received_at,
            )
            s.add(order)
            await s.flush()
            s.add(
                Fill(
                    order_id=order.id,
                    portfolio_id=portfolio_id,
                    symbol=symbol,
                    side=side,
                    price=exec_price,
                    qty=qty,
                    notional=notional,
                    realized_pnl=realized,
                    quote_price=obs.price,
                    quoted_at=obs.observed_at,
                    filled_at=filled_at,
                )
            )
            await s.commit()
            cash_after = pf.cash

        self.prices.track([symbol])
        return FillResult(
            symbol=symbol,
            side=side,
            qty=qty,
            price=exec_price,
            quote_price=obs.price,
            notional=notional,
            cash_after=cash_after,
            position_qty_after=qty_after,
            avg_cost_after=avg_after,
            realized_pnl=realized,
            realized_pct=realized_pct,
            received_at=received_at,
            quoted_at=obs.observed_at,
            filled_at=filled_at,
            closed_position=closed,
        )

    async def _record_rejection(
        self, portfolio_id, symbol, side, shares, dollars, interaction_id, received_at, reason: str
    ) -> None:
        try:
            async with self.db.session() as s:
                s.add(
                    Order(
                        portfolio_id=portfolio_id,
                        symbol=symbol,
                        side=side,
                        req_shares=shares,
                        req_dollars=dollars,
                        status="rejected",
                        reject_reason=reason[:200],
                        interaction_id=interaction_id,
                        received_at=received_at,
                    )
                )
                await s.commit()
        except Exception:
            log.debug("could not record rejection", exc_info=True)
