"""Shared bits for the copilot's tool modules: the per-request context, JSON shaping, field whitelists."""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from versus.bot.services import Services

log = logging.getLogger(__name__)

NO_PARTY = "No party in this channel. `/party create` opens one."
NOT_IN_ROUND = "You're not in this round. `/join` before it starts."


@dataclass(frozen=True)
class CopilotContext:
    svc: Services
    user_id: int
    channel_id: int
    round_id: int | None = None
    portfolio_id: int | None = None


def _num(x: Decimal | float | None) -> float | int | None:
    if x is None:
        return None
    if isinstance(x, int) and not isinstance(x, bool):
        return x
    if isinstance(x, (float, Decimal)):
        return round(float(x), 4)
    return x  # strings, bools: untouched


def _dump(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


def _error(msg: str) -> str:
    return _dump({"error": msg})


def _symbols(raw: list[str] | str | None, cap: int) -> list[str]:
    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else raw
    out: list[str] = []
    for s in items:
        s = str(s).strip().upper()
        if s and s not in out:
            out.append(s)
    return out[:cap]


def _one(raw: str) -> str | None:
    syms = _symbols(raw, 1)
    return syms[0] if syms else None


def pick(row: dict, fields: dict[str, str]) -> dict:
    """Whitelist `row` down to `fields` (FMP name -> short label), numbers rounded, nulls dropped."""
    out = {}
    for src, label in fields.items():
        v = _num(row.get(src))
        if v is not None and v != "":
            out[label] = v
    return out


def pct(a, b) -> float | None:
    """a / b as a percentage, or None when it can't be computed."""
    try:
        return round(float(a) / float(b) * 100, 2) if a is not None and b else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def strip_html(text: str | None) -> str:
    """Tags out, entities decoded, whitespace collapsed."""
    text = re.sub(r"<[^>]*>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def untrusted(tag: str, body: str) -> str:
    """Third-party prose (news, transcripts) goes to the model fenced so the prompt can tell it apart."""
    return f"<{tag}>\n{body}\n</{tag}>"


async def _quiet(coro: Awaitable, what: str, default=None):
    """Optional data: a 4xx (ETFs have no ratios) must not sink the whole answer."""
    try:
        return await coro
    except Exception:
        log.debug("%s unavailable", what, exc_info=True)
        return {} if default is None else default
