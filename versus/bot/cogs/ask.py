"""/ask: the copilot. Read-only, rate limited, never trades."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from versus.copilot.graph import DISABLED_MESSAGE, CopilotDisabled, ask
from versus.copilot.ratelimit import SlidingWindowLimiter
from versus.copilot.tools import CopilotContext

from ..embeds import ask_embed

if TYPE_CHECKING:
    from ..main import VersusBot

log = logging.getLogger(__name__)

QUESTION_LIMIT = 1000


class AskCog(commands.Cog):
    def __init__(self, bot: VersusBot) -> None:
        self.bot = bot
        self.limiter = SlidingWindowLimiter(bot.svc.settings.ask_rate_per_hour)

    @app_commands.command(name="ask", description="Ask the copilot about a stock, the news, or your game")
    @app_commands.describe(
        question="What do you want to know?", public="Post the answer in the channel instead of only to you"
    )
    async def ask(self, interaction: discord.Interaction, question: str, public: bool = False) -> None:
        ephemeral = not public
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        svc = self.bot.svc
        if not svc.settings.fireworks_api_key:
            await interaction.followup.send(DISABLED_MESSAGE, ephemeral=True)
            return
        wait = self.limiter.acquire(interaction.user.id)
        if wait > 0:
            await interaction.followup.send(
                f"Easy there. {svc.settings.ask_rate_per_hour} questions an hour; try again in {math.ceil(wait)}s.",
                ephemeral=True,
            )
            return
        question = question.strip()[:QUESTION_LIMIT]
        st = await svc.rounds.active(interaction.channel_id)
        pf = await svc.rounds.portfolio_for(st.round.id, interaction.user.id) if st else None
        ctx = CopilotContext(
            svc=svc,
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            round_id=st.round.id if st else None,
            portfolio_id=pf.id if pf else None,
        )
        try:
            result = await ask(ctx, question, cfg=svc.settings)
        except CopilotDisabled as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except TimeoutError:
            log.warning("ask timed out for user %s", interaction.user.id)
            await interaction.followup.send(
                "The copilot took too long to answer. Try a narrower question.", ephemeral=True
            )
            return
        except Exception:
            log.exception("ask failed for user %s", interaction.user.id)
            await interaction.followup.send(
                "The copilot hit an error talking to the model. Try again in a minute.", ephemeral=True
            )
            return
        model_short = result.model.rsplit("/", 1)[-1]
        embed = ask_embed(question, result.answer, model_short, len(result.tool_calls))
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
