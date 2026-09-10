"""FMP analyst endpoints: grades-consensus, price-target-consensus, ratings-snapshot, analyst-estimates,
earnings."""

from __future__ import annotations

import asyncio
from datetime import date

from langchain_core.tools import BaseTool, tool

from ._base import CopilotContext, _dump, _error, _num, _one, _quiet, pct, pick

GRADES = {
    "strongBuy": "strong_buy",
    "buy": "buy",
    "hold": "hold",
    "sell": "sell",
    "strongSell": "strong_sell",
    "consensus": "consensus",
}
TARGETS = {
    "targetLow": "low",
    "targetHigh": "high",
    "targetConsensus": "consensus",
    "targetMedian": "median",
}
SCORES = {
    "rating": "rating",
    "overallScore": "overall",
    "discountedCashFlowScore": "dcf",
    "returnOnEquityScore": "roe",
    "debtToEquityScore": "debt_to_equity",
    "priceToEarningsScore": "pe",
    "priceToBookScore": "pb",
}
ESTIMATE_YEARS = 3
ESTIMATE_FETCH = 12
EARNINGS_HISTORY = 4
EARNINGS_FETCH = 8


def _year(d: str | None) -> int | None:
    try:
        return int(str(d)[:4])
    except (TypeError, ValueError):
        return None


async def analyst_view(ctx: CopilotContext, symbol: str) -> dict:
    fmp = ctx.svc.fmp
    today = ctx.svc.rounds.clock().date()
    grades, targets, scores, estimates, quotes = await asyncio.gather(
        _quiet(fmp.row("grades-consensus", symbol=symbol), "grades"),
        _quiet(fmp.row("price-target-consensus", symbol=symbol), "targets"),
        _quiet(fmp.row("ratings-snapshot", symbol=symbol), "ratings"),
        _quiet(
            fmp.rows("analyst-estimates", symbol=symbol, period="annual", limit=ESTIMATE_FETCH),
            "estimates",
            default=[],
        ),
        _quiet(fmp.batch_quotes([symbol]), "quote"),
    )
    if not (grades or targets or scores or estimates):
        return {"error": f"{symbol}: no analyst coverage on FMP (ETFs have none)"}
    price = quotes[symbol].price if symbol in quotes else None
    target = pick(targets, TARGETS)
    if price and target.get("consensus"):
        target["upside_pct"] = round((float(target["consensus"]) / float(price) - 1) * 100, 2)
    upcoming = sorted(
        (e for e in estimates if (_year(e.get("date")) or 0) >= today.year), key=lambda e: e["date"]
    )
    return {
        "symbol": symbol,
        "price": _num(price),
        "analyst_grades": pick(grades, GRADES),
        "price_target": target,
        "fmp_scores_1_to_5": pick(scores, SCORES),
        "estimates": [
            {
                "fiscal_year_end": e.get("date"),
                "revenue_avg": e.get("revenueAvg"),
                "eps_avg": _num(e.get("epsAvg")),
                "eps_low": _num(e.get("epsLow")),
                "eps_high": _num(e.get("epsHigh")),
                "analysts": e.get("numAnalystsEps"),
            }
            for e in upcoming[:ESTIMATE_YEARS]
        ],
        "note": "grades count analyst ratings; scores are FMP's own 1 (weak) to 5 (strong) factor scores",
    }


async def earnings(ctx: CopilotContext, symbol: str) -> dict:
    rows = await ctx.svc.fmp.rows("earnings", symbol=symbol, limit=EARNINGS_FETCH)
    if not rows:
        return {"error": f"{symbol}: no earnings history"}
    today = ctx.svc.rounds.clock().date()
    rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
    reported = [r for r in rows if r.get("epsActual") is not None][:EARNINGS_HISTORY]
    future = [r for r in rows if r.get("epsActual") is None and str(r.get("date", "")) >= today.isoformat()]
    nxt = min(future, key=lambda r: r["date"], default=None)
    history = []
    for r in reported:
        eps_a, eps_e = r.get("epsActual"), r.get("epsEstimated")
        history.append(
            {
                "date": r.get("date"),
                "eps": _num(eps_a),
                "eps_estimate": _num(eps_e),
                "eps_surprise_pct": pct(eps_a - eps_e, abs(eps_e)) if eps_e else None,
                "revenue": r.get("revenueActual"),
                "revenue_estimate": r.get("revenueEstimated"),
                "beat": (eps_a >= eps_e) if eps_e is not None else None,
            }
        )
    return {
        "symbol": symbol,
        "next_report": {
            "date": nxt.get("date"),
            "eps_estimate": _num(nxt.get("epsEstimated")),
            "revenue_estimate": nxt.get("revenueEstimated"),
            "days_away": (date.fromisoformat(nxt["date"]) - today).days,
        }
        if nxt
        else None,
        "recent": history,
        "beats": sum(1 for h in history if h["beat"]),
        "of": sum(1 for h in history if h["beat"] is not None),
    }


def build(ctx: CopilotContext) -> list[BaseTool]:
    @tool(parse_docstring=True)
    async def get_analyst_view(symbol: str) -> str:
        """What Wall Street thinks of one stock: buy/hold/sell counts, consensus price target and implied
        upside, FMP's factor scores, and EPS and revenue estimates for the next fiscal years. Use it for
        "what do analysts say about X", "what's the price target", "is X expected to grow".

        Args:
            symbol: One ticker.
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        return _dump(await analyst_view(ctx, sym))

    @tool(parse_docstring=True)
    async def get_earnings(symbol: str) -> str:
        """Earnings track record and the next report date: last four quarters with EPS and revenue vs
        estimates (beat or miss) plus what is expected next. Use it for "when does X report", "did X
        beat last quarter", "does X usually beat".

        Args:
            symbol: One ticker.
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        return _dump(await earnings(ctx, sym))

    return [get_analyst_view, get_earnings]
