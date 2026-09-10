"""The round itself: the caller's portfolio, the standings, the rules. Nothing here touches FMP directly."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from versus.db import Round
from versus.engine.valuation import build_price_lookup, held_symbols, portfolio_view, standings

from ._base import NO_PARTY, NOT_IN_ROUND, CopilotContext, _dump, _error, _num


async def caller_holdings(ctx: CopilotContext) -> list[str]:
    """Symbols the caller holds right now, or [] when they have no portfolio here."""
    if ctx.round_id is None or ctx.portfolio_id is None:
        return []
    svc = ctx.svc
    lookup = await build_price_lookup(svc.db, svc.prices, await held_symbols(svc.db, [ctx.round_id]))
    view = await portfolio_view(svc.db, lookup, ctx.portfolio_id)
    if view is None or view.owner_id != ctx.user_id:
        return []
    return [h.symbol for h in view.holdings]


def build(ctx: CopilotContext) -> list[BaseTool]:
    svc = ctx.svc

    async def _lookup(symbols: list[str]):
        svc.prices.track(symbols)
        return await build_price_lookup(svc.db, svc.prices, symbols)

    @tool(parse_docstring=True)
    async def get_portfolio() -> str:
        """The caller's own paper portfolio in this channel's round: cash, equity, return, rank, and each
        position with quantity, average cost, last price and unrealized P&L. Call it before discussing
        "my positions" or "how am I doing".
        """
        if ctx.round_id is None:
            return _error(NO_PARTY)
        if ctx.portfolio_id is None:
            return _error(NOT_IN_ROUND)
        lookup = await _lookup(await held_symbols(svc.db, [ctx.round_id]))
        view = await portfolio_view(svc.db, lookup, ctx.portfolio_id)
        if view is None or view.owner_id != ctx.user_id:
            return _error(NOT_IN_ROUND)
        return _dump(
            {
                "player": view.display_name,
                "cash": _num(view.cash),
                "equity": _num(view.equity),
                "starting_cash": _num(view.starting_cash),
                "return_pct": _num(view.return_pct * 100),
                "rank": view.rank,
                "fills": view.fills,
                "positions": [
                    {
                        "symbol": h.symbol,
                        "qty": _num(h.qty),
                        "avg_cost": _num(h.avg_cost),
                        "last": _num(h.last),
                        "market_value": _num(h.market_value),
                        "unrealized": _num(h.unrealized),
                        "unrealized_pct": _num(h.unrealized_pct * 100),
                        "weight_pct": _num(h.weight * 100),
                        "price_is_stale": h.stale,
                    }
                    for h in view.holdings
                ],
            }
        )

    @tool(parse_docstring=True)
    async def get_leaderboard() -> str:
        """Current standings of the round in this channel: rank, player, equity, return and top holding.
        Other players' full positions are not available to anyone.
        """
        if ctx.round_id is None:
            return _error(NO_PARTY)
        lookup = await _lookup(await held_symbols(svc.db, [ctx.round_id]))
        board = await standings(svc.db, lookup, ctx.round_id)
        return _dump(
            {
                "players": len(board),
                "standings": [
                    {
                        "rank": s.rank,
                        "player": s.display_name,
                        "equity": _num(s.equity),
                        "return_pct": _num(s.return_pct * 100),
                        "top_holding": s.top_holding,
                        "top_weight_pct": _num(s.top_weight * 100) if s.top_weight is not None else None,
                        "is_caller": s.owner_id == ctx.user_id,
                    }
                    for s in board
                ],
            }
        )

    @tool(parse_docstring=True)
    async def get_rules() -> str:
        """This round's rules and timing: window, starting cash, fill model, universe limits, scoring."""
        if ctx.round_id is None:
            return _error(NO_PARTY)
        async with svc.db.session() as s:
            rnd = await s.get(Round, ctx.round_id)
        if rnd is None:
            return _error(NO_PARTY)
        rules = svc.rules_for(rnd)
        return _dump(
            {
                "status": rnd.status,
                "window": rules.preset_label,
                "sessions": rules.sessions,
                "starts_at": rnd.start_at.isoformat() if rnd.start_at else None,
                "ends_at": rnd.end_at.isoformat() if rnd.end_at else None,
                "starting_cash": _num(rules.starting_cash),
                "orders": "market only, fractional shares, long-only, no margin, $0 commission",
                "fill": f"first fresh price at least {rules.fill_delay_s:.0f}s after the order",
                "spread_bps": rules.spread_bps,
                "universe": {
                    "exchanges": "NYSE, Nasdaq, NYSE American; plain ETFs; no leveraged or inverse ETFs",
                    "min_price": _num(rules.min_price),
                    "min_market_cap": rules.min_market_cap,
                    "min_avg_volume": rules.min_avg_volume,
                },
                "order_size_cap_pct_of_adv": _num(rules.adv_cap_pct * 100),
                "min_notional": _num(rules.min_notional),
                "scoring": "total return; ties: fewer fills, then earlier join",
                "how_to_trade": "/buy and /sell slash commands only",
            }
        )

    return [get_portfolio, get_leaderboard, get_rules]
