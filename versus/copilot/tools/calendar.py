"""FMP calendars: earnings-calendar, dividends-calendar, splits-calendar. Without a symbol list the
calendar is filtered to the caller's holdings, or to S&P 500 members when they hold nothing, because the
raw week-of-earnings feed is ~900 rows of mostly foreign micro-caps."""

from __future__ import annotations

import weakref
from datetime import date, timedelta
from typing import Literal

from langchain_core.tools import BaseTool, tool

from ._base import CopilotContext, _dump, _num, _symbols
from .game import caller_holdings

Kind = Literal["earnings", "dividends", "splits"]
PATHS = {"earnings": "earnings-calendar", "dividends": "dividends-calendar", "splits": "splits-calendar"}
MAX_DAYS = 30
MAX_ROWS = 25
MAX_SYMBOLS = 25
SP500_TTL = timedelta(hours=24)

# FMP client -> (fetched_at, S&P 500 symbols). Keyed weakly so test fakes never share a cache.
_sp500: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


async def sp500(ctx: CopilotContext) -> set[str]:
    fmp = ctx.svc.fmp
    now = ctx.svc.rounds.clock()
    hit = _sp500.get(fmp)
    if hit and now - hit[0] < SP500_TTL:
        return hit[1]
    rows = await fmp.rows("sp500-constituent")
    syms = {r["symbol"] for r in rows if r.get("symbol")}
    _sp500[fmp] = (now, syms)
    return syms


def _shape(kind: str, r: dict) -> dict:
    if kind == "earnings":
        return {
            "symbol": r.get("symbol"),
            "date": r.get("date"),
            "eps_estimate": _num(r.get("epsEstimated")),
            "revenue_estimate": r.get("revenueEstimated"),
            "reported": r.get("epsActual") is not None,
            "eps_actual": _num(r.get("epsActual")),
        }
    if kind == "dividends":
        return {
            "symbol": r.get("symbol"),
            "ex_date": r.get("date"),
            "payment_date": r.get("paymentDate") or None,
            "dividend": _num(r.get("dividend")),
            "yield_pct": _num(r.get("yield")),
            "frequency": r.get("frequency") or None,
        }
    return {
        "symbol": r.get("symbol"),
        "date": r.get("date"),
        "ratio": f"{r.get('numerator')}:{r.get('denominator')}",
        "type": r.get("splitType"),
    }


async def calendar(ctx: CopilotContext, kind: str, symbols: list[str], days: int) -> dict:
    if kind not in PATHS:
        return {"error": f"kind must be one of {', '.join(PATHS)}"}
    days = max(1, min(int(days), MAX_DAYS))
    start: date = ctx.svc.rounds.clock().date()
    end = start + timedelta(days=days)
    rows = await ctx.svc.fmp.rows(PATHS[kind], from_=start.isoformat(), to=end.isoformat())
    if symbols:
        keep, scope = set(symbols), "requested symbols"
    else:
        held = await caller_holdings(ctx)
        if held:
            keep, scope = set(held), "your holdings"
        else:
            keep, scope = await sp500(ctx), "S&P 500 members"
    hits = sorted(
        (r for r in rows if r.get("symbol") in keep), key=lambda r: (r.get("date", ""), r["symbol"])
    )
    return {
        "kind": kind,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "scope": scope,
        "matches": len(hits),
        "events": [_shape(kind, r) for r in hits[:MAX_ROWS]],
        "not_in_window": sorted(keep - {r["symbol"] for r in hits}) if symbols else None,
    }


def build(ctx: CopilotContext) -> list[BaseTool]:
    @tool(parse_docstring=True)
    async def get_calendar(kind: Kind = "earnings", symbols: list[str] | None = None, days: int = 7) -> str:
        """Upcoming earnings reports, ex-dividend dates or stock splits over the next N days. With no
        symbols it covers the caller's holdings, or S&P 500 names when they hold nothing. Use it for
        "who reports this week", "any earnings in my portfolio", "when does X go ex-dividend".

        Args:
            kind: earnings, dividends or splits.
            symbols: Tickers to check, up to 25. Omit for the caller's holdings / the S&P 500.
            days: Days ahead to look, 1 to 30.
        """
        return _dump(await calendar(ctx, kind, _symbols(symbols, MAX_SYMBOLS), days))

    return [get_calendar]
