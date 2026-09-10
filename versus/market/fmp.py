"""Financial Modeling Prep client. Only the handful of endpoints the game needs.

Endpoint shapes verified live on 2026-09-09 against the /stable API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx

log = logging.getLogger(__name__)


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
