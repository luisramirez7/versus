"""Shared slash-command autocompleters."""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands

from versus.market.universe import ALLOWED_EXCHANGES, ETF_EXCHANGES

log = logging.getLogger(__name__)

TTL_S = 300
_MAX_ENTRIES = 512
_cache: dict[str, tuple[float, list[app_commands.Choice[str]]]] = {}


async def symbol_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Ticker suggestions from FMP search, limited to the exchanges the game trades on."""
    q = current.strip().upper()
    if not q:
        return []
    hit = _cache.get(q)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    try:
        rows = await interaction.client.svc.fmp.search(q, limit=15)  # type: ignore[attr-defined]
    except Exception:
        log.debug("autocomplete search failed", exc_info=True)
        return [app_commands.Choice(name=q, value=q)]
    choices = []
    for r in rows:
        if r.exchange not in ETF_EXCHANGES and r.exchange not in ALLOWED_EXCHANGES:
            continue
        label = f"{r.symbol} — {r.name}"
        choices.append(app_commands.Choice(name=label[:100], value=r.symbol))
    if not any(c.value == q for c in choices):
        choices.insert(0, app_commands.Choice(name=q, value=q))
    choices = choices[:25]
    if len(_cache) >= _MAX_ENTRIES:
        _cache.clear()
    _cache[q] = (time.monotonic(), choices)
    return choices
