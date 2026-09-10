"""The pinned leaderboard: one message per party, edited in place, debounced."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord

from versus.engine.valuation import build_price_lookup, held_symbols, standings

from .embeds import board_embed

if TYPE_CHECKING:
    from .main import VersusBot

log = logging.getLogger(__name__)


class BoardManager:
    def __init__(self, bot: VersusBot, debounce_s: float = 3.0) -> None:
        self.bot = bot
        self.debounce_s = debounce_s
        self._pending: dict[int, asyncio.Task] = {}

    def mark_dirty(self, party_id: int) -> None:
        if party_id in self._pending and not self._pending[party_id].done():
            return
        self._pending[party_id] = asyncio.create_task(self._debounced(party_id))

    async def _debounced(self, party_id: int) -> None:
        await asyncio.sleep(self.debounce_s)
        try:
            await self.refresh(party_id)
        except Exception:
            log.exception("board refresh failed for party %s", party_id)

    async def refresh(self, party_id: int, *, repost: bool = False) -> discord.Message | None:
        svc = self.bot.svc
        st = await svc.rounds.state(party_id)
        symbols = await held_symbols(svc.db, [st.round.id])
        svc.prices.track(symbols)
        lookup = await build_price_lookup(svc.db, svc.prices, symbols)
        board = await standings(svc.db, lookup, st.round.id)
        embed = board_embed(st, board, datetime.now(UTC))

        channel = self.bot.get_channel(st.party.channel_id) or await self.bot.fetch_channel(
            st.party.channel_id
        )
        msg: discord.Message | None = None
        if st.party.board_message_id and not repost:
            try:
                msg = await channel.fetch_message(st.party.board_message_id)
                await msg.edit(embed=embed)
                return msg
            except discord.NotFound:
                msg = None
            except discord.HTTPException:
                log.warning("could not edit board message", exc_info=True)
                return None
        msg = await channel.send(embed=embed)
        try:
            await msg.pin(reason="Versus leaderboard")
        except discord.HTTPException:
            log.info("could not pin board (missing Manage Messages?)")
        await svc.rounds.set_messages(party_id, board_message_id=msg.id)
        return msg

    async def unpin(self, party_id: int) -> None:
        st = await self.bot.svc.rounds.state(party_id)
        if not st.party.board_message_id:
            return
        try:
            channel = self.bot.get_channel(st.party.channel_id) or await self.bot.fetch_channel(
                st.party.channel_id
            )
            msg = await channel.fetch_message(st.party.board_message_id)
            await msg.unpin()
        except discord.HTTPException:
            pass
