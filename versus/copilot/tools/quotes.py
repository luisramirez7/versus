"""FMP quote and chart endpoints: batch-quote-short, quote, stock-price-change, historical-price-eod,
technical-indicators/rsi."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from langchain_core.tools import BaseTool, tool

from ._base import CopilotContext, _dump, _error, _num, _one, _quiet, _symbols, pick

Range = Literal["1w", "1m", "3m", "6m", "1y"]
RANGE_DAYS: dict[str, int] = {"1w": 7, "1m": 31, "3m": 92, "6m": 183, "1y": 366}
MAX_QUOTE_SYMBOLS = 10
LAST_CLOSES = 10
RSI_LOOKBACK_DAYS = 30

QUOTE_FIELDS = {
    "price": "price",
    "changePercentage": "change_pct",
    "volume": "volume",
    "dayLow": "day_low",
    "dayHigh": "day_high",
    "yearLow": "year_low",
    "yearHigh": "year_high",
    "priceAvg50": "sma_50",
    "priceAvg200": "sma_200",
    "previousClose": "previous_close",
}
RETURN_FIELDS = {
    "1D": "1d",
    "5D": "5d",
    "1M": "1m",
    "3M": "3m",
    "6M": "6m",
    "ytd": "ytd",
    "1Y": "1y",
    "3Y": "3y",
    "5Y": "5y",
}


async def technicals(ctx: CopilotContext, symbol: str) -> dict:
    fmp = ctx.svc.fmp
    today = ctx.svc.rounds.clock().date()
    quote, change, rsi_rows = await asyncio.gather(
        _quiet(fmp.row("quote", symbol=symbol), "quote"),
        _quiet(fmp.row("stock-price-change", symbol=symbol), "price change"),
        _quiet(
            fmp.rows(
                "technical-indicators/rsi",
                symbol=symbol,
                periodLength=14,
                timeframe="1day",
                from_=(today - timedelta(days=RSI_LOOKBACK_DAYS)).isoformat(),
                to=today.isoformat(),
            ),
            "rsi",
            default=[],
        ),
    )
    if not quote:
        return {"error": f"{symbol}: no quote"}
    out = {"symbol": symbol, "name": quote.get("name"), **pick(quote, QUOTE_FIELDS)}
    price, hi, lo = quote.get("price"), quote.get("yearHigh"), quote.get("yearLow")
    if price and hi and lo and hi > lo:
        out["pct_below_52w_high"] = round((1 - float(price) / float(hi)) * 100, 2)
        out["pct_of_52w_range"] = round((float(price) - float(lo)) / (float(hi) - float(lo)) * 100, 1)
    for k, label in (("priceAvg50", "vs_sma_50_pct"), ("priceAvg200", "vs_sma_200_pct")):
        if price and quote.get(k):
            out[label] = round((float(price) / float(quote[k]) - 1) * 100, 2)
    latest = max(rsi_rows, key=lambda r: r.get("date", ""), default=None)
    if latest and latest.get("rsi") is not None:
        out["rsi_14"] = _num(latest["rsi"])
        out["rsi_date"] = str(latest.get("date", ""))[:10]
    out["returns_pct"] = pick(change, RETURN_FIELDS)
    out["note"] = (
        "RSI above 70 is often read as overbought, below 30 as oversold; sma = simple moving average"
    )
    return out


def build(ctx: CopilotContext) -> list[BaseTool]:
    svc = ctx.svc

    @tool(parse_docstring=True)
    async def get_quote(symbols: list[str]) -> str:
        """Latest price, day change and volume for up to 10 tickers. Use it for "what is X at" and to
        compare tickers right now.

        Args:
            symbols: Ticker symbols, e.g. ["NVDA", "AAPL"].
        """
        syms = _symbols(symbols, MAX_QUOTE_SYMBOLS)
        if not syms:
            return _error("no symbols given")
        quotes = await svc.fmp.batch_quotes(syms)
        rows = []
        for s in syms:
            q = quotes.get(s)
            if q is None:
                continue
            prev = q.price - q.change if q.change is not None else None
            rows.append(
                {
                    "symbol": s,
                    "price": _num(q.price),
                    "change": _num(q.change),
                    "change_pct": _num(q.change / prev * 100) if prev else None,
                    "volume": q.volume,
                }
            )
        return _dump({"quotes": rows, "not_found": [s for s in syms if s not in quotes]})

    @tool(parse_docstring=True)
    async def get_history_summary(symbol: str, range: Range = "1m") -> str:
        """How a ticker has moved over a period: start and end close, % change, high, low, average volume
        and the last 10 closes. Use it for "how has X done this month" or to see the recent path of the
        price. For plain multi-period returns get_technicals is cheaper.

        Args:
            symbol: One ticker.
            range: 1w, 1m, 3m, 6m or 1y.
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        days = RANGE_DAYS.get(range)
        if days is None:
            return _error(f"range must be one of {', '.join(RANGE_DAYS)}")
        today = svc.rounds.clock().date()
        rows = await svc.fmp.historical_eod(sym, today - timedelta(days=days), today)
        bars = sorted(
            (r for r in rows if r.get("close") is not None and r.get("date")), key=lambda r: r["date"]
        )
        if not bars:
            return _error(f"{sym}: no price history for {range}")
        first, last = bars[0], bars[-1]
        vols = [int(b["volume"]) for b in bars if b.get("volume") is not None]
        change = Decimal(str(last["close"])) / Decimal(str(first["close"])) - 1 if first["close"] else None
        return _dump(
            {
                "symbol": sym,
                "range": range,
                "sessions": len(bars),
                "start": {"date": first["date"], "close": _num(first["close"])},
                "end": {"date": last["date"], "close": _num(last["close"])},
                "change_pct": _num(change * 100) if change is not None else None,
                "high": _num(max(float(b.get("high") or b["close"]) for b in bars)),
                "low": _num(min(float(b.get("low") or b["close"]) for b in bars)),
                "avg_volume": int(sum(vols) / len(vols)) if vols else None,
                "last_closes": [[b["date"], _num(b["close"])] for b in bars[-LAST_CLOSES:]],
            }
        )

    @tool(parse_docstring=True)
    async def get_technicals(symbol: str) -> str:
        """Momentum snapshot for one ticker: returns over 1 day to 5 years, 52-week range and where the
        price sits in it, distance from the 50 and 200 day moving averages, and 14-day RSI. Use it for
        "is X overbought", "is X near its high", "how has X done this year".

        Args:
            symbol: One ticker, e.g. "NVDA".
        """
        sym = _one(symbol)
        if sym is None:
            return _error("no symbol given")
        return _dump(await technicals(ctx, sym))

    return [get_quote, get_history_summary, get_technicals]
