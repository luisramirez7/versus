"""FMP company endpoints: profile, key-metrics-ttm, ratios-ttm, stock-peers, etf/info, etf/holdings."""

from __future__ import annotations

import asyncio

from langchain_core.tools import BaseTool, tool

from ._base import CopilotContext, _dump, _error, _num, _one, _quiet, pick, strip_html

# FMP field -> short label. Field names verified live on 2026-09-09.
KEY_METRICS: dict[str, str] = {
    "enterpriseValueTTM": "enterprise_value",
    "evToEBITDATTM": "ev_to_ebitda",
    "evToSalesTTM": "ev_to_sales",
    "returnOnEquityTTM": "roe",
    "returnOnInvestedCapitalTTM": "roic",
    "earningsYieldTTM": "earnings_yield",
    "freeCashFlowYieldTTM": "fcf_yield",
    "netDebtToEBITDATTM": "net_debt_to_ebitda",
    "currentRatioTTM": "current_ratio",
}
RATIOS: dict[str, str] = {
    "priceToEarningsRatioTTM": "pe",
    "priceToSalesRatioTTM": "ps",
    "priceToBookRatioTTM": "pb",
    "priceToFreeCashFlowRatioTTM": "p_fcf",
    "grossProfitMarginTTM": "gross_margin",
    "operatingProfitMarginTTM": "operating_margin",
    "netProfitMarginTTM": "net_margin",
    "debtToEquityRatioTTM": "debt_to_equity",
    "dividendYieldTTM": "dividend_yield",
    "revenuePerShareTTM": "revenue_per_share",
    "netIncomePerShareTTM": "eps",
    "freeCashFlowPerShareTTM": "fcf_per_share",
}
ETF_INFO = {
    "name": "name",
    "etfCompany": "issuer",
    "assetClass": "asset_class",
    "expenseRatio": "expense_ratio_pct",
    "assetsUnderManagement": "aum",
    "holdingsCount": "holdings_count",
    "inceptionDate": "inception",
    "avgVolume": "avg_volume",
}
MAX_PEERS = 8
MAX_ETF_HOLDINGS = 10
MAX_ETF_SECTORS = 6
DESCRIPTION_CHARS = 240


async def peers(ctx: CopilotContext, symbol: str) -> dict:
    fmp = ctx.svc.fmp
    rows = await fmp.rows("stock-peers", symbol=symbol)
    rows = sorted(rows, key=lambda r: -float(r.get("mktCap") or 0))[:MAX_PEERS]  # FMP's list has odd tails
    if not rows:
        return {"error": f"{symbol}: no peers listed"}
    syms = [symbol] + [r["symbol"] for r in rows if r.get("symbol")]
    quotes = await _quiet(fmp.batch_quotes(syms), "peer quotes")

    def entry(sym: str, name: str | None, mcap) -> dict:
        q = quotes.get(sym)
        prev = q.price - q.change if q and q.change is not None else None
        return {
            "symbol": sym,
            "name": name,
            "price": _num(q.price) if q else None,
            "change_pct": _num(q.change / prev * 100) if q and prev else None,
            "market_cap": mcap,
        }

    return {
        "symbol": symbol,
        "peers": [entry(r["symbol"], r.get("companyName"), r.get("mktCap")) for r in rows if r.get("symbol")],
        "note": "call get_company or get_financials on a peer to compare valuation in depth",
    }


async def etf(ctx: CopilotContext, symbol: str) -> dict:
    fmp = ctx.svc.fmp
    info, holdings = await asyncio.gather(
        _quiet(fmp.row("etf/info", symbol=symbol), "etf info"),
        _quiet(fmp.rows("etf/holdings", symbol=symbol), "etf holdings", default=[]),
    )
    if not info and not holdings:
        return {"error": f"{symbol}: not an ETF FMP knows"}
    sectors = sorted(info.get("sectorsList") or [], key=lambda s: -float(s.get("exposure") or 0))
    top = sorted(holdings, key=lambda h: -float(h.get("weightPercentage") or 0))[:MAX_ETF_HOLDINGS]
    return {
        "symbol": symbol,
        **pick(info, ETF_INFO),
        "description": strip_html(info.get("description"))[:DESCRIPTION_CHARS] or None,
        "sectors_pct": {s["industry"]: _num(s.get("exposure")) for s in sectors[:MAX_ETF_SECTORS]},
        "top_holdings": [
            {"symbol": h.get("asset"), "name": h.get("name"), "weight_pct": _num(h.get("weightPercentage"))}
            for h in top
        ],
        "top_10_weight_pct": _num(sum(float(h.get("weightPercentage") or 0) for h in top)),
    }


def build(ctx: CopilotContext) -> list[BaseTool]:
    svc = ctx.svc

    @tool(parse_docstring=True)
    async def get_company(symbol: str) -> str:
        """Company profile plus trailing-twelve-month valuation and quality metrics: P/E, P/S, margins, ROE,
        debt, dividend yield. Use it for "is X expensive", "how profitable is X", or a quick overview.

        Args:
            symbol: One ticker, e.g. "NVDA".
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        profile = await svc.fmp.profile(sym)
        if profile is None:
            return _error(f"{sym}: no profile found")
        metrics, ratios = await asyncio.gather(
            _quiet(svc.fmp.key_metrics_ttm(sym), "key metrics"), _quiet(svc.fmp.ratios_ttm(sym), "ratios")
        )
        ttm = {**pick(metrics, KEY_METRICS), **pick(ratios, RATIOS)}
        return _dump(
            {
                "symbol": profile.symbol,
                "name": profile.name,
                "exchange": profile.exchange,
                "is_etf": profile.is_etf,
                "actively_trading": profile.is_actively_trading,
                "price": _num(profile.price),
                "market_cap": profile.market_cap,
                "avg_volume": profile.avg_volume,
                "ttm": ttm,
                "note": "margins, yields and returns are fractions (0.27 = 27%); ETFs have no ratios, use get_etf",
            }
        )

    @tool(parse_docstring=True)
    async def get_peers(symbol: str) -> str:
        """Competitors of a company with price, day change and market cap. Use it for "who competes with
        X", "compare X to its rivals", or to find a similar name.

        Args:
            symbol: One ticker.
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        return _dump(await peers(ctx, sym))

    @tool(parse_docstring=True)
    async def get_etf(symbol: str) -> str:
        """What an ETF holds: issuer, expense ratio, assets, sector mix, top 10 holdings and how
        concentrated it is. Use it for "what's in SPY", "is QQQ all tech", or to compare two ETFs.

        Args:
            symbol: One ETF ticker, e.g. "SPY".
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        return _dump(await etf(ctx, sym))

    return [get_company, get_peers, get_etf]
