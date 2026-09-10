from __future__ import annotations

from datetime import datetime
from decimal import Decimal

DISCLAIMER = "Paper trading with virtual money. Not a broker or investment adviser. Not investment advice."


def money(x: Decimal | float) -> str:
    return f"${Decimal(x):,.2f}"


def signed_money(x: Decimal) -> str:
    sign = "+" if x >= 0 else "−"
    return f"{sign}${abs(Decimal(x)):,.2f}"


def pct(x: Decimal) -> str:
    sign = "+" if x >= 0 else "−"
    return f"{sign}{abs(Decimal(x)) * 100:.2f}%"


def qty(x: Decimal) -> str:
    x = Decimal(x).normalize()
    return f"{x:f}" if x != x.to_integral() else f"{int(x)}"


def ts(dt: datetime, style: str = "f") -> str:
    """Discord renders these in each viewer's own timezone."""
    return f"<t:{int(dt.timestamp())}:{style}>"


def arrow(side: str) -> str:
    return "▲" if side == "buy" else "▼"


def trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def compact(x: float | None, prefix: str = "$") -> str:
    """5417511070000 -> $5.42T, 4894465080 -> $4.89B, 300000000 -> $300M. None -> —."""
    if x is None:
        return "—"
    n = float(x)
    sign = "-" if n < 0 else ""
    n = abs(n)
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            v = n / div
            return f"{sign}{prefix}{v:,.2f}{suffix}" if v < 10 else f"{sign}{prefix}{v:,.0f}{suffix}"
    return f"{sign}{prefix}{n:,.0f}"


def spct(x: float | None, digits: int = 2) -> str:
    """A value already in percent, signed: 9.0 -> +9.00%. None -> —."""
    if x is None:
        return "—"
    sign = "+" if x >= 0 else "−"
    return f"{sign}{abs(float(x)):.{digits}f}%"
