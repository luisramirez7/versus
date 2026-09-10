"""/leaderboard and /rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..embeds import rules_embed

if TYPE_CHECKING:
    from ..main import VersusBot


class InfoCog(commands.Cog):
    def __init__(self, bot: VersusBot) -> None:
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Post (and re-pin) the standings")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        st = await self.bot.svc.rounds.active(interaction.channel_id)
        if st is None:
            await interaction.response.send_message("No party in this channel.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        msg = await self.bot.board.refresh(st.party.id, repost=True)
        if msg is None:
            await interaction.followup.send(
                "Couldn't post the board. Check the bot can send messages here.", ephemeral=True
            )
        else:
            await interaction.followup.send("Board posted and pinned.", ephemeral=True)

    @app_commands.command(name="rules", description="This round's rules")
    async def rules(self, interaction: discord.Interaction) -> None:
        st = await self.bot.svc.rounds.active(interaction.channel_id)
        if st is None:
            await interaction.response.send_message("No party in this channel.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=rules_embed(self.bot.svc.rules_for(st.round), st), ephemeral=True
        )
