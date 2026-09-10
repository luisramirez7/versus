"""FMP market-wide endpoints: biggest-gainers, biggest-losers, most-actives, sector-performance-snapshot,
company-screener, batch-quote for indexes and commodities, treasury-rates, economic-indicators.
Movers and screens are filtered through the game's universe so every name returned is tradeable."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal

from langchain_core.tools import BaseTool, tool

from versus.market.universe import ALLOWED_EXCHANGES

from ._base import CopilotContext, _dump, _num, _quiet, pick

Movers = Literal["gainers", "losers", "active", "sectors"]
MOVER_PATHS = {"gainers": "biggest-gainers", "losers": "biggest-losers", "active": "most-actives"}
MAX_MOVERS = 10
SECTOR_LOOKBACK_DAYS = 5
MAX_SCREEN = 10
SECTORS = (
    "Technology, Healthcare, Financial Services, Consumer Cyclical, Consumer Defensive, Industrials, "
    "Energy, Basic Materials, Communication Services, Real Estate, Utilities"
)
MACRO_QUOTES = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^DJI": "Dow Jones",
    "^RUT": "Russell 2000",
    "^VIX": "VIX",
    "GCUSD": "Gold",
    "CLUSD": "WTI crude",
    "BTCUSD": "Bitcoin",
}
TREASURY = {"month3": "3m", "year2": "2y", "year10": "10y", "year30": "30y"}
INDICATORS = {"CPI": "cpi_index", "unemploymentRate": "unemployment_pct", "federalFunds": "fed_funds_pct"}
SCREEN_FIELDS = {
    "symbol": "symbol",
    "companyName": "name",
    "sector": "sector",
    "industry": "industry",
    "price": "price",
    "marketCap": "market_cap",
    "beta": "beta",
    "avgVolume": "avg_volume",
    "lastAnnualDividend": "annual_dividend",
}


async def movers(ctx: CopilotContext, kind: str) -> dict:
    svc = ctx.svc
    if kind == "sectors":
        today = svc.rounds.clock().date()
        for back in range(SECTOR_LOOKBACK_DAYS):
            d = today - timedelta(days=back)
            rows = await _quiet(
                svc.fmp.rows("sector-performance-snapshot", date=d.isoformat()), "sectors", default=[]
            )
            if rows:
                rows = sorted(rows, key=lambda r: -float(r.get("averageChange") or 0))
                return {
                    "kind": "sectors",
                    "date": d.isoformat(),
                    "sectors": [
                        {"sector": r["sector"], "avg_change_pct": _num(r["averageChange"])} for r in rows
                    ],
                }
        return {"error": "no sector performance published for the last few days"}
    if kind not in MOVER_PATHS:
        return {"error": f"kind must be one of {', '.join(MOVER_PATHS)}, sectors"}
    rules = svc.universe.rules
    rows = await svc.fmp.rows(MOVER_PATHS[kind])
    cheap = [
        r
        for r in rows
        if r.get("exchange") in ALLOWED_EXCHANGES
        and r.get("price") is not None
        and r["price"] >= rules.min_price
    ]
    out, skipped = [], 0
    for r in cheap:
        if len(out) >= MAX_MOVERS:
            break
        elig = await svc.universe.check(r["symbol"], svc.rounds.clock())
        if not elig.eligible:
            skipped += 1
            continue
        out.append(
            {
                "symbol": r["symbol"],
                "name": r.get("name"),
                "price": _num(r.get("price")),
                "change_pct": _num(r.get("changesPercentage")),
                "is_etf": elig.is_etf,
            }
        )
    return {
        "kind": kind,
        "movers": out,
        "note": f"filtered to names tradeable in this game ({len(rows) - len(cheap) + skipped} penny stocks, "
        "OTC listings and other ineligible names dropped)",
    }


async def screen(
    ctx: CopilotContext,
    sector: str | None,
    industry: str | None,
    min_market_cap: int | None,
    max_market_cap: int | None,
    etfs: bool,
    limit: int,
) -> dict:
    rules = ctx.svc.universe.rules
    n = max(1, min(int(limit), MAX_SCREEN))
    params = {
        "sector": sector or None,
        "industry": industry or None,
        "marketCapMoreThan": max(int(min_market_cap or 0), rules.min_market_cap),
        "marketCapLowerThan": int(max_market_cap) if max_market_cap else None,
        "priceMoreThan": float(rules.min_price),
        "volumeMoreThan": rules.min_avg_volume,
        "exchange": ",".join(sorted(ALLOWED_EXCHANGES)),
        "isActivelyTrading": "true",
        "isEtf": "true" if etfs else "false",
        "limit": n,
    }
    rows = await ctx.svc.fmp.rows("company-screener", **params)
    return {
        "filters": {k: v for k, v in params.items() if v is not None and k != "limit"},
        "results": [pick(r, SCREEN_FIELDS) for r in rows[:n]],
        "note": "sorted by market cap, largest first; universe minimums already applied",
    }


async def macro(ctx: CopilotContext) -> dict:
    fmp = ctx.svc.fmp
    quotes, rates, *indicators = await asyncio.gather(
        _quiet(fmp.rows("batch-quote", symbols=",".join(MACRO_QUOTES)), "index quotes", default=[]),
        _quiet(fmp.rows("treasury-rates"), "treasury", default=[]),
        *(_quiet(fmp.rows("economic-indicators", name=n), n, default=[]) for n in INDICATORS),
    )
    by_symbol = {q.get("symbol"): q for q in quotes}
    markets = []
    for sym, label in MACRO_QUOTES.items():
        q = by_symbol.get(sym)
        if q and q.get("price") is not None:
            markets.append(
                {
                    "name": label,
                    "symbol": sym,
                    "price": _num(q["price"]),
                    "change_pct": _num(q.get("changePercentage")),
                    "year_low": _num(q.get("yearLow")),
                    "year_high": _num(q.get("yearHigh")),
                }
            )
    latest_rates = max(rates, key=lambda r: r.get("date", ""), default={})
    econ = {}
    for name, rows in zip(INDICATORS, indicators):
        latest = max(rows, key=lambda r: r.get("date", ""), default=None)
        if latest:
            econ[INDICATORS[name]] = {"value": _num(latest.get("value")), "as_of": latest.get("date")}
    return {
        "markets": markets,
        "treasury_yields_pct": {"as_of": latest_rates.get("date"), **pick(latest_rates, TREASURY)},
        "economy": econ,
        "note": "VIX above ~20 is a nervous market; a 10y yield above 2y is a normal (un-inverted) curve",
    }


def build(ctx: CopilotContext) -> list[BaseTool]:
    @tool(parse_docstring=True)
    async def get_movers(kind: Movers = "gainers") -> str:
        """Today's biggest gainers, losers or most-traded stocks, already filtered to names this game
        allows, or today's sector performance ranking. Use it for "what's hot today", "what's getting
        crushed", "which sectors are up".

        Args:
            kind: gainers, losers, active or sectors.
        """
        return _dump(await movers(ctx, kind))

    @tool(parse_docstring=True)
    async def screen_stocks(
        sector: str | None = None,
        industry: str | None = None,
        min_market_cap: int | None = None,
        max_market_cap: int | None = None,
        etfs: bool = False,
        limit: int = 10,
    ) -> str:
        """Find tradeable stocks by sector, industry and market cap, largest first. Use it for "give me
        some healthcare names", "small-cap energy stocks", "big tech ETFs". Sectors: Technology,
        Healthcare, Financial Services, Consumer Cyclical, Consumer Defensive, Industrials, Energy, Basic
        Materials, Communication Services, Real Estate, Utilities.

        Args:
            sector: One FMP sector name from the list above, or omit.
            industry: Free-text industry such as "Semiconductors" or "Biotechnology", or omit.
            min_market_cap: In dollars, e.g. 2000000000 for $2B. Omit for the game minimum.
            max_market_cap: In dollars, e.g. 10000000000 for $10B. Omit for no cap.
            etfs: True to list ETFs instead of companies.
            limit: How many, at most 10.
        """
        return _dump(await screen(ctx, sector, industry, min_market_cap, max_market_cap, etfs, limit))

    @tool(parse_docstring=True)
    async def get_macro() -> str:
        """The big picture in one call: S&P 500, Nasdaq, Dow, Russell, VIX, gold, oil and bitcoin with
        day change and 52-week range, the treasury yield curve, and the latest CPI, unemployment and fed
        funds readings. Use it for "how's the market", "is the market risk-on", "what are rates doing".
        """
        return _dump(await macro(ctx))

    return [get_movers, screen_stocks, get_macro]
