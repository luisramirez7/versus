"""Market data slash commands: /movers, /earnings, /macro, /analyst, /whoowns, /screen.

Each one calls a copilot fetcher directly (no model in the loop) and renders an embed, so the same
trimmed data /ask sees is one command away."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from versus.copilot.tools import CopilotContext
from versus.copilot.tools.analyst import analyst_view, earnings
from versus.copilot.tools.calendar import calendar
from versus.copilot.tools.market import macro, movers, screen
from versus.copilot.tools.ownership import congress, insiders, institutions

from ..autocomplete import symbol_autocomplete
from ..embeds import (
    analyst_embed,
    calendar_embed,
    earnings_embed,
    macro_embed,
    movers_embed,
    ownership_embed,
    screen_embed,
)

if TYPE_CHECKING:
    from ..main import VersusBot

log = logging.getLogger(__name__)

PROVIDER_DOWN = "Couldn't reach the market data provider. Try again in a moment."
Sector = Literal[
    "Technology",
    "Healthcare",
    "Financial Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Industrials",
    "Energy",
    "Basic Materials",
    "Communication Services",
    "Real Estate",
    "Utilities",
]


class MarketCog(commands.Cog):
    def __init__(self, bot: VersusBot) -> None:
        self.bot = bot

    async def _ctx(self, interaction: discord.Interaction) -> CopilotContext:
        svc = self.bot.svc
        st = await svc.rounds.active(interaction.channel_id)
        pf = await svc.rounds.portfolio_for(st.round.id, interaction.user.id) if st else None
        return CopilotContext(
            svc=svc,
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            round_id=st.round.id if st else None,
            portfolio_id=pf.id if pf else None,
        )

    async def _run(self, interaction: discord.Interaction, what: str, coro) -> dict | None:
        """Await a fetcher; on failure or a data error tell the caller and return None."""
        try:
            data = await coro
        except Exception:
            log.exception("%s failed", what)
            await interaction.followup.send(PROVIDER_DOWN)
            return None
        if isinstance(data, dict) and "error" in data:
            await interaction.followup.send(data["error"])
            return None
        return data

    @app_commands.command(
        name="movers", description="Today's biggest gainers, losers, most active, or sectors"
    )
    @app_commands.describe(kind="What to rank")
    async def movers(
        self,
        interaction: discord.Interaction,
        kind: Literal["gainers", "losers", "active", "sectors"] = "gainers",
    ) -> None:
        await interaction.response.defer(thinking=True)
        data = await self._run(interaction, "movers", movers(await self._ctx(interaction), kind))
        if data is not None:
            await interaction.followup.send(embed=movers_embed(data))

    @app_commands.command(name="earnings", description="A stock's earnings record, or who reports soon")
    @app_commands.describe(
        symbol="Ticker for its track record; omit for the calendar",
        days="Calendar window in days when no ticker is given (default 7)",
    )
    @app_commands.autocomplete(symbol=symbol_autocomplete)
    async def earnings(
        self,
        interaction: discord.Interaction,
        symbol: str | None = None,
        days: app_commands.Range[int, 1, 30] = 7,
    ) -> None:
        await interaction.response.defer(thinking=True)
        ctx = await self._ctx(interaction)
        if symbol:
            data = await self._run(interaction, "earnings", earnings(ctx, symbol.strip().upper()))
            if data is not None:
                await interaction.followup.send(embed=earnings_embed(data))
        else:
            data = await self._run(interaction, "calendar", calendar(ctx, "earnings", [], days))
            if data is not None:
                await interaction.followup.send(embed=calendar_embed(data))

    @app_commands.command(
        name="macro", description="Indexes, VIX, gold, oil, bitcoin, yields and the economy"
    )
    async def macro(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        data = await self._run(interaction, "macro", macro(await self._ctx(interaction)))
        if data is not None:
            await interaction.followup.send(embed=macro_embed(data))

    @app_commands.command(name="analyst", description="Analyst consensus, price target and estimates")
    @app_commands.describe(symbol="Ticker")
    @app_commands.autocomplete(symbol=symbol_autocomplete)
    async def analyst(self, interaction: discord.Interaction, symbol: str) -> None:
        await interaction.response.defer(thinking=True)
        data = await self._run(
            interaction, "analyst", analyst_view(await self._ctx(interaction), symbol.strip().upper())
        )
        if data is not None:
            await interaction.followup.send(embed=analyst_embed(data))

    @app_commands.command(name="whoowns", description="Insiders, Congress and big funds trading a stock")
    @app_commands.describe(symbol="Ticker")
    @app_commands.autocomplete(symbol=symbol_autocomplete)
    async def whoowns(self, interaction: discord.Interaction, symbol: str) -> None:
        await interaction.response.defer(thinking=True)
        sym = symbol.strip().upper()
        ctx = await self._ctx(interaction)
        try:
            ins, con, inst = await asyncio.gather(
                insiders(ctx, sym), congress(ctx, sym), institutions(ctx, sym)
            )
        except Exception:
            log.exception("whoowns failed")
            await interaction.followup.send(PROVIDER_DOWN)
            return
        await interaction.followup.send(embed=ownership_embed(sym, ins, con, inst))

    @app_commands.command(name="screen", description="Find tradeable stocks by sector, industry and size")
    @app_commands.describe(
        sector="Sector",
        industry='Industry keyword, e.g. "Semiconductors" or "Biotechnology"',
        min_cap_billions="Smallest market cap in $ billions",
        max_cap_billions="Largest market cap in $ billions",
        etfs="List ETFs instead of companies",
    )
    async def screen(
        self,
        interaction: discord.Interaction,
        sector: Sector | None = None,
        industry: str | None = None,
        min_cap_billions: app_commands.Range[float, 0.0] | None = None,
        max_cap_billions: app_commands.Range[float, 0.0] | None = None,
        etfs: bool = False,
    ) -> None:
        await interaction.response.defer(thinking=True)
        data = await self._run(
            interaction,
            "screen",
            screen(
                await self._ctx(interaction),
                sector,
                industry,
                int(min_cap_billions * 1e9) if min_cap_billions else None,
                int(max_cap_billions * 1e9) if max_cap_billions else None,
                etfs,
                10,
            ),
        )
        if data is not None:
            await interaction.followup.send(embed=screen_embed(data))
