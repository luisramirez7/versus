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
