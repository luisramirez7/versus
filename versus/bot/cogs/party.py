"""/party create|start|status|cancel|end, /join, /leave, and the Join button."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from versus.engine.rounds import RoundError

from ..embeds import live_message, lobby_embed, scheduled_message, status_embed

if TYPE_CHECKING:
    from ..main import VersusBot

log = logging.getLogger(__name__)


class JoinView(discord.ui.View):
    """Persistent view: survives restarts because the custom_id is fixed."""

    def __init__(self, bot: VersusBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, custom_id="versus:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await join_party(self.bot, interaction)


async def join_party(bot: VersusBot, interaction: discord.Interaction) -> None:
    svc = bot.svc
    st = await svc.rounds.active(interaction.channel_id)
    if st is None:
        await interaction.response.send_message(
            "No party in this channel. `/party create` to start one.", ephemeral=True
        )
        return
    try:
        await svc.rounds.join(st.party.id, interaction.user.id, interaction.user.display_name)
    except RoundError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    await interaction.response.send_message("You're in. Wait for the host to start.", ephemeral=True)
    await refresh_lobby(bot, st.party.id)


async def refresh_lobby(bot: VersusBot, party_id: int) -> None:
    svc = bot.svc
    st = await svc.rounds.state(party_id)
    if not st.party.lobby_message_id:
        return
    try:
        channel = bot.get_channel(st.party.channel_id) or await bot.fetch_channel(st.party.channel_id)
        msg = await channel.fetch_message(st.party.lobby_message_id)
        await msg.edit(embed=lobby_embed(st))
    except discord.HTTPException:
        log.debug("lobby message edit failed", exc_info=True)


async def open_lobby(bot: VersusBot, interaction: discord.Interaction, *, cash: int, preset: str) -> None:
    """Create a party hosted by the interacting user and post the lobby. Shared by /party create and Run it back."""
    svc = bot.svc
    try:
        st = await svc.rounds.create(
            guild_id=interaction.guild_id or 0,
            channel_id=interaction.channel_id,
            host_user_id=interaction.user.id,
            cash=cash,
            preset=preset,
        )
        await svc.rounds.join(st.party.id, interaction.user.id, interaction.user.display_name)
    except RoundError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    st = await svc.rounds.state(st.party.id)
    await interaction.response.send_message(embed=lobby_embed(st), view=JoinView(bot))
    msg = await interaction.original_response()
    await svc.rounds.set_messages(st.party.id, lobby_message_id=msg.id)


class PartyCog(commands.Cog):
    def __init__(self, bot: VersusBot) -> None:
        self.bot = bot

    party = app_commands.Group(
        name="party", description="Create and run a paper-trading match in this channel"
    )

    @party.command(name="create", description="Open a lobby in this channel")
    @app_commands.describe(cash="Starting cash for everyone", preset="How long the round runs")
    async def create(
        self,
        interaction: discord.Interaction,
        cash: Literal[1000, 10000, 100000] = 1000,
        preset: Literal["day", "week", "month", "quarter"] = "day",
    ) -> None:
        await open_lobby(self.bot, interaction, cash=cash, preset=preset)

    @party.command(name="start", description="Start the round (host only)")
    async def start(self, interaction: discord.Interaction) -> None:
        svc = self.bot.svc
        st = await svc.rounds.active(interaction.channel_id)
        if st is None:
            await interaction.response.send_message("No party here. `/party create` first.", ephemeral=True)
            return
        try:
            st = await svc.rounds.start(st.party.id, interaction.user.id)
        except RoundError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        if st.party.status == "live":
            await interaction.response.send_message(live_message(st))
            await self.bot.board.refresh(st.party.id, repost=True)
        else:
            await interaction.response.send_message(scheduled_message(st))
        await refresh_lobby(self.bot, st.party.id)

    @party.command(name="status", description="Where the round stands")
    async def status(self, interaction: discord.Interaction) -> None:
        st = await self.bot.svc.rounds.active(interaction.channel_id)
        if st is None:
            await interaction.response.send_message(
                "No party here. `/party create` to open one.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=status_embed(st), ephemeral=True)

    @party.command(name="cancel", description="Cancel a party that hasn't gone live (host only)")
    async def cancel(self, interaction: discord.Interaction) -> None:
        svc = self.bot.svc
        st = await svc.rounds.active(interaction.channel_id)
        if st is None:
            await interaction.response.send_message("Nothing to cancel.", ephemeral=True)
            return
        try:
            await svc.rounds.cancel(st.party.id, interaction.user.id)
        except RoundError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message("Party cancelled.")

    @party.command(name="end", description="End a live round now and settle at current prices (host only)")
    async def end(self, interaction: discord.Interaction) -> None:
        svc = self.bot.svc
        st = await svc.rounds.active(interaction.channel_id)
        if st is None:
            await interaction.response.send_message("No party here.", ephemeral=True)
            return
        try:
            await svc.rounds.end_now(st.party.id, interaction.user.id)
        except RoundError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message("Ending the round. Settling now…")
        await self.bot.scheduler.tick()

    @app_commands.command(name="join", description="Join this channel's party")
    async def join(self, interaction: discord.Interaction) -> None:
        await join_party(self.bot, interaction)

    @app_commands.command(name="leave", description="Leave the party before it starts")
    async def leave(self, interaction: discord.Interaction) -> None:
        svc = self.bot.svc
        st = await svc.rounds.active(interaction.channel_id)
        if st is None:
            await interaction.response.send_message("No party here.", ephemeral=True)
            return
        try:
            await svc.rounds.leave(st.party.id, interaction.user.id)
        except RoundError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message("You left the party.", ephemeral=True)
        await refresh_lobby(self.bot, st.party.id)
