"""/buy, /sell, /portfolio, /quote, /history."""

from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from versus.db import FeedMessage, Fill
from versus.engine.fills import OrderError
from versus.engine.valuation import build_price_lookup, held_symbols, portfolio_view
from versus.market.universe import ALLOWED_EXCHANGES, ETF_EXCHANGES

from ..embeds import feed_line, fill_confirmation, portfolio_embed, quote_embed
from ..format import money, qty, signed_money, ts
from ..recap import FEED_REACTIONS

if TYPE_CHECKING:
    from ..main import VersusBot

log = logging.getLogger(__name__)


def _dec(x: float | None) -> Decimal | None:
    if x is None:
        return None
    try:
        return Decimal(str(x))
    except InvalidOperation:
        return None


class TradeCog(commands.Cog):
    def __init__(self, bot: VersusBot) -> None:
        self.bot = bot
        self._ac_cache: dict[str, tuple[float, list[app_commands.Choice[str]]]] = {}

    # ----- autocomplete -----
    async def symbol_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        q = current.strip().upper()
        if len(q) < 1:
            return []
        hit = self._ac_cache.get(q)
        if hit and time.monotonic() - hit[0] < 300:
            return hit[1]
        try:
            rows = await self.bot.svc.fmp.search(q, limit=15)
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
        self._ac_cache[q] = (time.monotonic(), choices)
        return choices

    # ----- helpers -----
    async def _member_context(self, interaction: discord.Interaction):
        svc = self.bot.svc
        st = await svc.rounds.active(interaction.channel_id)
        if st is None:
            return None, None, "No party in this channel. `/party create` to open one."
        pf = await svc.rounds.portfolio_for(st.round.id, interaction.user.id)
        if pf is None:
            return st, None, "You're not in this round. `/join` before it starts."
        return st, pf, None

    async def _order(
        self, interaction: discord.Interaction, side: str, symbol: str, shares, dollars, sell_all=False
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        st, pf, err = await self._member_context(interaction)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        if st.party.status != "live":
            when = f" It starts {ts(st.round.start_at, 'R')}." if st.round.start_at else ""
            await interaction.followup.send(f"The round isn't live yet.{when}", ephemeral=True)
            return
        svc = self.bot.svc
        try:
            r = await svc.engine.market_order(
                round_id=st.round.id,
                portfolio_id=pf.id,
                symbol=symbol,
                side=side,
                rules=svc.rules_for(st.round),
                shares=_dec(shares),
                dollars=_dec(dollars),
                sell_all=sell_all,
                interaction_id=interaction.id,
            )
        except OrderError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        await interaction.followup.send(fill_confirmation(r), ephemeral=True)
        try:
            msg = await interaction.channel.send(feed_line(interaction.user.display_name, r))
            await self._remember_feed(msg, st.round.id, pf.id, r)
            for emoji in FEED_REACTIONS:
                await msg.add_reaction(emoji)
        except discord.HTTPException:
            log.debug("feed line failed", exc_info=True)
        self.bot.board.mark_dirty(st.party.id)

    async def _remember_feed(self, msg: discord.Message, round_id: int, portfolio_id: int, r) -> None:
        svc = self.bot.svc
        async with svc.db.session() as s:
            fill_id = (
                await s.execute(
                    select(Fill.id)
                    .where(
                        Fill.portfolio_id == portfolio_id,
                        Fill.symbol == r.symbol,
                        Fill.filled_at == r.filled_at,
                    )
                    .order_by(Fill.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if fill_id is None:
                return
            s.add(
                FeedMessage(fill_id=fill_id, round_id=round_id, channel_id=msg.channel.id, message_id=msg.id)
            )
            await s.commit()

    # ----- commands -----
    @app_commands.command(name="buy", description="Market buy at the next fresh price")
    @app_commands.describe(
        symbol="Ticker", shares="Number of shares (fractional ok)", dollars="Dollar amount instead of shares"
    )
    @app_commands.autocomplete(symbol=symbol_autocomplete)
    async def buy(
        self,
        interaction: discord.Interaction,
        symbol: str,
        shares: float | None = None,
        dollars: float | None = None,
    ) -> None:
        await self._order(interaction, "buy", symbol, shares, dollars)

    @app_commands.command(name="sell", description="Market sell at the next fresh price")
    @app_commands.describe(
        symbol="Ticker",
        shares="Number of shares",
        dollars="Dollar amount instead of shares",
        all="Sell the whole position",
    )
    @app_commands.autocomplete(symbol=symbol_autocomplete)
    async def sell(
        self,
        interaction: discord.Interaction,
        symbol: str,
        shares: float | None = None,
        dollars: float | None = None,
        all: bool = False,
    ) -> None:
        await self._order(interaction, "sell", symbol, shares, dollars, sell_all=all)

    @app_commands.command(name="portfolio", description="Your positions, cash, and rank (only you see this)")
    @app_commands.describe(member="Look at someone else's portfolio")
    async def portfolio(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        svc = self.bot.svc
        st = await svc.rounds.active(interaction.channel_id)
        if st is None:
            await interaction.followup.send("No party in this channel.", ephemeral=True)
            return
        target = member or interaction.user
        pf = await svc.rounds.portfolio_for(st.round.id, target.id)
        if pf is None:
            who = "You're" if target == interaction.user else f"{target.display_name} is"
            await interaction.followup.send(f"{who} not in this round.", ephemeral=True)
            return
        symbols = await held_symbols(svc.db, [st.round.id])
        svc.prices.track(symbols)
        lookup = await build_price_lookup(svc.db, svc.prices, symbols)
        view = await portfolio_view(svc.db, lookup, pf.id)
        await interaction.followup.send(embed=portfolio_embed(view), ephemeral=True)

    @app_commands.command(name="quote", description="Price and whether a symbol is tradeable")
    @app_commands.autocomplete(symbol=symbol_autocomplete)
    async def quote(self, interaction: discord.Interaction, symbol: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        svc = self.bot.svc
        symbol = symbol.upper().strip()
        elig = await svc.universe.check(symbol)
        price = change = None
        try:
            quotes = await svc.fmp.batch_quotes([symbol])
            if symbol in quotes:
                price, change = quotes[symbol].price, quotes[symbol].change
        except Exception:
            log.debug("quote failed", exc_info=True)
        await interaction.followup.send(embed=quote_embed(symbol, price, change, elig), ephemeral=True)

    @app_commands.command(name="history", description="Your last fills (only you see this)")
    @app_commands.describe(member="Someone else's fills")
    async def history(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        svc = self.bot.svc
        st = await svc.rounds.active(interaction.channel_id)
        if st is None:
            await interaction.followup.send("No party in this channel.", ephemeral=True)
            return
        target = member or interaction.user
        pf = await svc.rounds.portfolio_for(st.round.id, target.id)
        if pf is None:
            await interaction.followup.send("Not in this round.", ephemeral=True)
            return
        async with svc.db.session() as s:
            fills = (
                (
                    await s.execute(
                        select(Fill)
                        .where(Fill.portfolio_id == pf.id)
                        .order_by(Fill.filled_at.desc())
                        .limit(15)
                    )
                )
                .scalars()
                .all()
            )
        if not fills:
            await interaction.followup.send("No fills yet.", ephemeral=True)
            return
        lines = []
        for f in fills:
            extra = f" {signed_money(f.realized_pnl)}" if f.realized_pnl is not None else ""
            lines.append(
                f"{ts(f.filled_at, 't')} {'▲' if f.side == 'buy' else '▼'} {qty(f.qty)} {f.symbol} @ {f.price:,.2f} ({money(f.notional)}{extra})"
            )
        await interaction.followup.send("\n".join(lines), ephemeral=True)
