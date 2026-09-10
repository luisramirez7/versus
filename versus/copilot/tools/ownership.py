"""FMP ownership endpoints: insider-trading/search, insider-trading/statistics, senate-trades,
house-trades, institutional-ownership (13F) summary and top holders."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from langchain_core.tools import BaseTool, tool

from ._base import CopilotContext, _dump, _error, _num, _one, _quiet, pick

MAX_INSIDERS = 10
MAX_CONGRESS = 10
MAX_HOLDERS = 5
FILING_LAG_DAYS = 45  # 13Fs are due 45 days after quarter end

INSIDER_STATS = {
    "year": "year",
    "quarter": "quarter",
    "acquiredTransactions": "buys",
    "disposedTransactions": "sells",
    "totalAcquired": "shares_bought",
    "totalDisposed": "shares_sold",
}
POSITIONS = {
    "investorsHolding": "investors_holding",
    "investorsHoldingChange": "investors_change",
    "ownershipPercent": "ownership_pct",
    "ownershipPercentChange": "ownership_change_pct",
    "increasedPositions": "increased",
    "reducedPositions": "reduced",
    "newPositions": "new",
    "closedPositions": "closed",
    "putCallRatio": "put_call_ratio",
}


def latest_13f_quarter(today: date) -> tuple[int, int]:
    """The most recent quarter whose 13F filings are (mostly) in."""
    d = today - timedelta(days=FILING_LAG_DAYS)
    q = (d.month - 1) // 3  # completed quarters this year
    return (d.year, q) if q else (d.year - 1, 4)


async def insiders(ctx: CopilotContext, symbol: str) -> dict:
    fmp = ctx.svc.fmp
    trades, stats = await asyncio.gather(
        _quiet(fmp.rows("insider-trading/search", symbol=symbol, limit=MAX_INSIDERS), "insiders", default=[]),
        _quiet(fmp.rows("insider-trading/statistics", symbol=symbol), "insider stats", default=[]),
    )
    if not trades and not stats:
        return {"error": f"{symbol}: no insider filings"}
    latest = max(stats, key=lambda r: (r.get("year", 0), r.get("quarter", 0)), default={})
    return {
        "symbol": symbol,
        "latest_quarter": pick(latest, INSIDER_STATS),
        "recent": [
            {
                "date": t.get("transactionDate"),
                "name": t.get("reportingName"),
                "role": t.get("typeOfOwner"),
                "type": t.get("transactionType"),
                "shares": t.get("securitiesTransacted"),
                "price": _num(t.get("price")),
                "value": _num(float(t["securitiesTransacted"]) * float(t["price"]))
                if t.get("securitiesTransacted") and t.get("price")
                else None,
                "owned_after": t.get("securitiesOwned"),
            }
            for t in sorted(trades, key=lambda t: t.get("transactionDate", ""), reverse=True)[:MAX_INSIDERS]
        ],
        "note": "S-Sale can be a scheduled 10b5-1 plan; P-Purchase on the open market is the stronger signal",
    }


async def congress(ctx: CopilotContext, symbol: str) -> dict:
    fmp = ctx.svc.fmp
    senate, house = await asyncio.gather(
        _quiet(fmp.rows("senate-trades", symbol=symbol), "senate", default=[]),
        _quiet(fmp.rows("house-trades", symbol=symbol), "house", default=[]),
    )
    rows = [dict(r, chamber="Senate") for r in senate] + [dict(r, chamber="House") for r in house]
    if not rows:
        return {"symbol": symbol, "trades": [], "note": "no congressional trades disclosed for this symbol"}
    rows.sort(key=lambda r: r.get("transactionDate", ""), reverse=True)
    return {
        "symbol": symbol,
        "disclosed_total": len(rows),
        "trades": [
            {
                "date": r.get("transactionDate"),
                "disclosed": r.get("disclosureDate"),
                "chamber": r["chamber"],
                "name": f"{r.get('firstName', '')} {r.get('lastName', '')}".strip(),
                "district": r.get("district"),
                "type": r.get("type"),
                "amount": r.get("amount"),
                "owner": r.get("owner") or None,
            }
            for r in rows[:MAX_CONGRESS]
        ],
        "note": "amounts are disclosure ranges; trades are reported up to 45 days late",
    }


async def institutions(ctx: CopilotContext, symbol: str) -> dict:
    fmp = ctx.svc.fmp
    year, quarter = latest_13f_quarter(ctx.svc.rounds.clock().date())
    summary, holders = await asyncio.gather(
        _quiet(
            fmp.row(
                "institutional-ownership/symbol-positions-summary", symbol=symbol, year=year, quarter=quarter
            ),
            "13f summary",
        ),
        _quiet(
            fmp.rows(
                "institutional-ownership/extract-analytics/holder",
                symbol=symbol,
                year=year,
                quarter=quarter,
                limit=MAX_HOLDERS,
            ),
            "13f holders",
            default=[],
        ),
    )
    if not summary and not holders:
        return {"error": f"{symbol}: no 13F data for Q{quarter} {year}"}
    return {
        "symbol": symbol,
        "quarter": f"Q{quarter} {year}",
        "summary": pick(summary, POSITIONS),
        "top_holders": [
            {
                "investor": h.get("investorName"),
                "ownership_pct": _num(h.get("ownership")),
                "shares_change_pct": _num(h.get("changeInSharesNumberPercentage")),
                "weight_in_their_portfolio_pct": _num(h.get("weight")),
                "is_new": h.get("isNew"),
            }
            for h in sorted(holders, key=lambda h: -float(h.get("ownership") or 0))[:MAX_HOLDERS]
        ],
    }


def build(ctx: CopilotContext) -> list[BaseTool]:
    @tool(parse_docstring=True)
    async def get_insiders(symbol: str) -> str:
        """Recent insider buys and sells (officers, directors, 10% holders) with the latest quarter's
        totals. Use it for "are insiders selling X", "did the CEO buy".

        Args:
            symbol: One ticker.
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        return _dump(await insiders(ctx, sym))

    @tool(parse_docstring=True)
    async def get_congress_trades(symbol: str) -> str:
        """Stock trades disclosed by US senators and representatives in one ticker: who, when, buy or
        sell, and the amount range. Use it for "which politicians trade X", "is Congress buying".

        Args:
            symbol: One ticker.
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        return _dump(await congress(ctx, sym))

    @tool(parse_docstring=True)
    async def get_institutions(symbol: str) -> str:
        """Institutional (13F) ownership for the latest reported quarter: how many funds hold it, whether
        they added or cut, put/call ratio, and the top 5 holders. Use it for "who owns X", "are funds
        buying X".

        Args:
            symbol: One ticker.
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        return _dump(await institutions(ctx, sym))

    return [get_insiders, get_congress_trades, get_institutions]
