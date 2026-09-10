"""/chart: a price chart PNG, with the caller's own fills overlaid while a round is on."""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from versus.charts.price import FillMarker, render_comparison, render_price_chart
from versus.charts.ranges import RANGES, fetch_bars, range_label
from versus.db import Fill

from ..autocomplete import symbol_autocomplete
from ..embeds import DOWN, UP
from ..format import DISCLAIMER

if TYPE_CHECKING:
    from ..main import VersusBot

log = logging.getLogger(__name__)

SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
MAX_COMPARE = 3
FOOTER = f"{DISCLAIMER} · Data: Financial Modeling Prep"
RangeKey = Literal["1d", "5d", "1m", "3m", "6m", "1y"]


class ChartCog(commands.Cog):
    def __init__(self, bot: VersusBot) -> None:
        self.bot = bot

    @app_commands.command(name="chart", description="Price chart, with your fills marked during a round")
    @app_commands.describe(
        symbol="Ticker",
        range="How far back (default: the latest session)",
        compare="Up to three more tickers to compare, comma-separated",
    )
    @app_commands.autocomplete(symbol=symbol_autocomplete)
    async def chart(
        self,
        interaction: discord.Interaction,
        symbol: str,
        range: RangeKey = "1d",
        compare: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        symbol = symbol.strip().upper()
        if not SYMBOL.match(symbol):
            await interaction.followup.send(f"`{symbol}` doesn't look like a ticker.")
            return
        extras = _parse_compare(compare, exclude=symbol)
        if len(extras) > MAX_COMPARE:
            await interaction.followup.send(f"Compare takes up to {MAX_COMPARE} symbols.")
            return
        if bad := [s for s in extras if not SYMBOL.match(s)]:
            await interaction.followup.send(f"`{bad[0]}` doesn't look like a ticker.")
            return

        svc = self.bot.svc
        now = datetime.now(UTC)
        try:
            bars = await fetch_bars(svc.fmp, svc.calendar, symbol, range, now)
        except Exception:
            log.exception("chart: fetching %s %s failed", symbol, range)
            await interaction.followup.send("Couldn't reach the market data provider. Try again in a moment.")
            return
        if not bars:
            await interaction.followup.send(await self._no_data_message(symbol, range))
            return
        label = range_label(range, bars)

        if extras:
            series = {symbol: bars}
            missing = []
            for extra in extras:
                try:
                    other = await fetch_bars(svc.fmp, svc.calendar, extra, range, now)
                except Exception:
                    log.exception("chart: fetching %s %s failed", extra, range)
                    other = []
                if other:
                    series[extra] = other
                else:
                    missing.append(extra)
            if len(series) < 2:
                await interaction.followup.send(f"No price data for {', '.join(missing)}.")
                return
            png = await asyncio.to_thread(render_comparison, series, label=label)
            title = f"{' vs '.join(series)} · {label}"
            color = UP if float(bars[-1].close) >= float(bars[0].close) else DOWN
            note = f"No data for {', '.join(missing)}." if missing else None
        else:
            fills = await self._own_fills(interaction, symbol)
            png = await asyncio.to_thread(render_price_chart, bars, symbol=symbol, label=label, fills=fills)
            title = f"{symbol} · {label}"
            color = UP if bars[-1].close >= bars[0].open else DOWN
            note = None

        embed = discord.Embed(title=title, color=color)
        embed.set_image(url="attachment://chart.png")
        embed.set_footer(text=FOOTER)
        await interaction.followup.send(
            content=note, embed=embed, file=discord.File(io.BytesIO(png), filename="chart.png")
        )

    # ----- helpers -----
    async def _own_fills(self, interaction: discord.Interaction, symbol: str) -> list[FillMarker]:
        """The caller's fills for `symbol` in this channel's active round, if any."""
        svc = self.bot.svc
        st = await svc.rounds.active(interaction.channel_id)
        if st is None:
            return []
        pf = await svc.rounds.portfolio_for(st.round.id, interaction.user.id)
        if pf is None:
            return []
        async with svc.db.session() as s:
            rows = (
                (
                    await s.execute(
                        select(Fill)
                        .where(Fill.portfolio_id == pf.id, Fill.symbol == symbol)
                        .order_by(Fill.id)
                    )
                )
                .scalars()
                .all()
            )
        return [FillMarker(f.filled_at, f.side, f.price) for f in rows]

    async def _no_data_message(self, symbol: str, range_key: str) -> str:
        try:
            elig = await self.bot.svc.universe.check(symbol)
        except Exception:
            log.debug("chart: eligibility lookup failed", exc_info=True)
            elig = None
        if elig is not None and elig.reason == "unknown symbol":
            return f"Unknown symbol `{symbol}`."
        return f"No price data for {symbol} over {RANGES[range_key].label}."


def _parse_compare(raw: str | None, *, exclude: str) -> list[str]:
    out: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        s = part.strip().upper()
        if s and s != exclude and s not in out:
            out.append(s)
    return out
