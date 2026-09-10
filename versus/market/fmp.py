"""Financial Modeling Prep client. Only the handful of endpoints the game needs.

Endpoint shapes verified live on 2026-09-09 against the /stable API (the /ask copilot reads too).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")  # FMP bar timestamps are New York wall time
INTRADAY_INTERVALS = ("1min", "5min", "15min", "30min", "1hour", "4hour")


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    change: Decimal | None
    volume: int | None


@dataclass(frozen=True)
class Profile:
    symbol: str
    name: str
    exchange: str
    is_etf: bool
    is_actively_trading: bool
    price: Decimal | None
    market_cap: int | None
    avg_volume: int | None


@dataclass(frozen=True)
class SearchHit:
    symbol: str
    name: str
    exchange: str


@dataclass(frozen=True)
class MarketHours:
    exchange: str
    is_open: bool
    opening: str
    closing: str
    timezone: str


@dataclass(frozen=True)
class Holiday:
    date: date
    name: str
    is_closed: bool
    adj_close_time: str | None  # "13:00" on half days


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar. `ts` is aware New York time: the bar's start for intraday, midnight for daily."""

    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def _bar(row: dict, ts: datetime) -> Bar | None:
    try:
        o, h, lo, c = (_dec(row[k]) for k in ("open", "high", "low", "close"))
    except KeyError:
        return None
    if None in (o, h, lo, c):
        return None
    return Bar(ts, o, h, lo, c, int(row.get("volume") or 0))


class FMPClient:
    def __init__(self, api_key: str, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params) -> list | dict:
        params["apikey"] = self.api_key
        r = await self._client.get(f"{self.base_url}/{path}", params=params)
        r.raise_for_status()
        return r.json()

    async def batch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i : i + 100]
            rows = await self._get("batch-quote-short", symbols=",".join(chunk))
            for row in rows or []:
                if row.get("price") is None:
                    continue
                out[row["symbol"]] = Quote(
                    symbol=row["symbol"],
                    price=_dec(row["price"]),
                    change=_dec(row.get("change")),
                    volume=int(row["volume"]) if row.get("volume") is not None else None,
                )
        return out

    async def profile(self, symbol: str) -> Profile | None:
        rows = await self._get("profile", symbol=symbol)
        if not rows:
            return None
        r = rows[0]
        return Profile(
            symbol=r["symbol"],
            name=r.get("companyName") or r["symbol"],
            exchange=r.get("exchange") or "",
            is_etf=bool(r.get("isEtf")),
            is_actively_trading=bool(r.get("isActivelyTrading", True)),
            price=_dec(r.get("price")),
            market_cap=int(r["marketCap"]) if r.get("marketCap") is not None else None,
            avg_volume=int(r["averageVolume"]) if r.get("averageVolume") is not None else None,
        )

    async def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        rows = await self._get("search-symbol", query=query, limit=limit)
        return [
            SearchHit(r["symbol"], r.get("name") or r["symbol"], r.get("exchange") or "") for r in rows or []
        ]

    async def market_hours(self, exchange: str = "NYSE") -> MarketHours:
        rows = await self._get("exchange-market-hours", exchange=exchange)
        r = rows[0]
        return MarketHours(
            r["exchange"], bool(r["isMarketOpen"]), r["openingHour"], r["closingHour"], r["timezone"]
        )

    async def holidays(self, exchange: str, from_date: date, to_date: date) -> list[Holiday]:
        rows = await self._get(
            "holidays-by-exchange",
            exchange=exchange,
            **{"from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        out = []
        for r in rows or []:
            out.append(
                Holiday(
                    date=date.fromisoformat(r["date"]),
                    name=r.get("name") or "",
                    is_closed=bool(r.get("isClosed")),
                    adj_close_time=r.get("adjCloseTime"),
                )
            )
        return out

    async def intraday(self, symbol: str, interval: str, from_date: date, to_date: date) -> list[Bar]:
        """Intraday bars, oldest first. Rows arrive newest first with "YYYY-MM-DD HH:MM:SS" NY wall time."""
        if interval not in INTRADAY_INTERVALS:
            raise ValueError(f"interval must be one of {INTRADAY_INTERVALS}, not {interval!r}")
        rows = await self._get(
            f"historical-chart/{interval}",
            symbol=symbol,
            **{"from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        bars = []
        for r in rows or []:
            ts = datetime.strptime(r["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=NY)
            if (b := _bar(r, ts)) is not None:
                bars.append(b)
        bars.sort(key=lambda b: b.ts)
        return bars

    async def historical_eod(self, symbol: str, from_date: date, to_date: date) -> list[dict]:
        """Daily rows, newest first: date, open, high, low, close, volume, change, changePercent, vwap.
        Verified live 2026-09-09: the path is historical-price-eod/full (the hyphenated form 404s)."""
        rows = await self._get(
            "historical-price-eod/full",
            symbol=symbol,
            **{"from": from_date.isoformat(), "to": to_date.isoformat()},
        )
        return list(rows or [])

    async def eod(self, symbol: str, from_date: date, to_date: date) -> list[Bar]:
        """Daily bars, oldest first, stamped at NY midnight."""
        bars = []
        for r in await self.historical_eod(symbol, from_date, to_date):
            ts = datetime.combine(date.fromisoformat(r["date"]), time(0), NY)
            if (b := _bar(r, ts)) is not None:
                bars.append(b)
        bars.sort(key=lambda b: b.ts)
        return bars

    # ----- /ask copilot reads (raw rows; the copilot tools trim them) -----
    async def rows(self, path: str, **params) -> list[dict]:
        """Any list endpoint, raw. The copilot's tool modules whitelist fields and cap rows; keep that
        trimming there, not here. `from_`/`to` map to FMP's `from`/`to`."""
        if "from_" in params:
            params["from"] = params.pop("from_")
        out = await self._get(path, **{k: v for k, v in params.items() if v is not None})
        if isinstance(out, dict):
            return [out]
        return list(out or [])

    async def row(self, path: str, **params) -> dict:
        """First row of a list endpoint, or {} when it is empty."""
        rows = await self.rows(path, **params)
        return dict(rows[0]) if rows else {}

    async def key_metrics_ttm(self, symbol: str) -> dict:
        """Trailing-twelve-month metrics: marketCap, enterpriseValueTTM, evToEBITDATTM, returnOnEquityTTM, ..."""
        rows = await self._get("key-metrics-ttm", symbol=symbol)
        return dict(rows[0]) if rows else {}

    async def ratios_ttm(self, symbol: str) -> dict:
        """Trailing-twelve-month ratios: priceToEarningsRatioTTM, netProfitMarginTTM, dividendYieldTTM, ..."""
        rows = await self._get("ratios-ttm", symbol=symbol)
        return dict(rows[0]) if rows else {}

    async def stock_news(self, symbols: list[str], limit: int = 5) -> list[dict]:
        """Rows: symbol, publishedDate, publisher, site, title, text, url, image."""
        rows = await self._get("news/stock", symbols=",".join(symbols), limit=limit)
        return list(rows or [])

    async def general_news(self, limit: int = 5) -> list[dict]:
        """Same row shape as stock_news, symbol is null."""
        rows = await self._get("news/general-latest", limit=limit)
        return list(rows or [])
