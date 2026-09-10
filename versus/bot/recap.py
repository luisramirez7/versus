"""End-of-round recap: reaction tallies, equity curve, the embed, and the Run-it-back button."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import discord
from sqlalchemy import select

from versus.charts.equity import render_equity_curve
from versus.db import FeedMessage, Fill, LeaderboardSnapshot, Portfolio
from versus.engine.rounds import PartyState, RoundError
from versus.engine.settlement import Settlement

from .embeds import recap_embed

if TYPE_CHECKING:
    from .main import VersusBot

log = logging.getLogger(__name__)

FIRE = "🔥"
CLOWN = "🤡"
FEED_REACTIONS = (FIRE, CLOWN)


@dataclass(frozen=True)
class Highlight:
    display_name: str
    symbol: str
    side: str
    count: int

    @property
    def label(self) -> str:
        verb = "buy" if self.side == "buy" else "sell"
        return f"{self.display_name}'s {self.symbol} {verb}"


@dataclass(frozen=True)
class RecapExtras:
    crowd_favorite: Highlight | None = None
    most_clowned: Highlight | None = None
    equity_png: bytes | None = None


def pick_highlights(tallies: list[tuple[Highlight, Highlight]]) -> tuple[Highlight | None, Highlight | None]:
    """Given (fire, clown) tallies per fill, the top of each; None when nobody reacted."""
    fire = max((f for f, _ in tallies), key=lambda h: h.count, default=None)
    clown = max((c for _, c in tallies), key=lambda h: h.count, default=None)
    return (fire if fire and fire.count > 0 else None, clown if clown and clown.count > 0 else None)


async def _reaction_tallies(bot: VersusBot, round_id: int) -> list[tuple[Highlight, Highlight]]:
    svc = bot.svc
    async with svc.db.session() as s:
        rows = (
            await s.execute(
                select(FeedMessage, Fill, Portfolio.display_name)
                .join(Fill, Fill.id == FeedMessage.fill_id)
                .join(Portfolio, Portfolio.id == Fill.portfolio_id)
                .where(FeedMessage.round_id == round_id)
            )
        ).all()
    if not rows:
        return []
    channel = bot.get_channel(rows[0][0].channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(rows[0][0].channel_id)
        except discord.HTTPException:
            return []
    out: list[tuple[Highlight, Highlight]] = []
    for fm, fill, name in rows:
        try:
            msg = await channel.fetch_message(fm.message_id)
        except discord.HTTPException:
            continue
        counts = {FIRE: 0, CLOWN: 0}
        for r in msg.reactions:
            if str(r.emoji) in counts:
                counts[str(r.emoji)] = r.count - (1 if r.me else 0)  # don't count our own seed reaction
        out.append(
            (
                Highlight(name, fill.symbol, fill.side, counts[FIRE]),
                Highlight(name, fill.symbol, fill.side, counts[CLOWN]),
            )
        )
    return out


async def _equity_png(bot: VersusBot, st: PartyState) -> bytes | None:
    svc = bot.svc
    async with svc.db.session() as s:
        rows = (
            await s.execute(
                select(LeaderboardSnapshot.portfolio_id, LeaderboardSnapshot.ts, LeaderboardSnapshot.equity)
                .where(LeaderboardSnapshot.round_id == st.round.id)
                .order_by(LeaderboardSnapshot.ts)
            )
        ).all()
    names = {m.id: m.display_name for m in st.members}
    series: dict[str, list[tuple]] = {}
    for pid, ts, equity in rows:
        series.setdefault(names.get(pid, str(pid)), []).append((ts, equity))
    try:
        return render_equity_curve(
            series, starting_cash=Decimal(st.round.starting_cash), title="Equity, whole round"
        )
    except Exception:
        log.exception("equity curve render failed")
        return None


async def build_extras(bot: VersusBot, st: PartyState) -> RecapExtras:
    try:
        fav, clown = pick_highlights(await _reaction_tallies(bot, st.round.id))
    except Exception:
        log.exception("reaction tally failed")
        fav = clown = None
    return RecapExtras(crowd_favorite=fav, most_clowned=clown, equity_png=await _equity_png(bot, st))


class RematchButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"versus:rematch:(?P<party_id>[0-9]+)"
):
    """Persistent 'Run it back' button: opens a new lobby with the finished party's settings."""

    def __init__(self, party_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Run it back", style=discord.ButtonStyle.primary, custom_id=f"versus:rematch:{party_id}"
            )
        )
        self.party_id = party_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["party_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        from .cogs.party import open_lobby  # local import: cogs import this module

        bot: VersusBot = interaction.client  # type: ignore[assignment]
        try:
            old = await bot.svc.rounds.state(self.party_id)
        except RoundError:
            await interaction.response.send_message("Couldn't find that party any more.", ephemeral=True)
            return
        await open_lobby(bot, interaction, cash=int(old.round.starting_cash), preset=old.round.preset)


async def post_recap(bot: VersusBot, st: PartyState, settlement: Settlement, channel) -> None:
    extras = await build_extras(bot, st)
    embed = recap_embed(st, settlement, extras)
    view = discord.ui.View(timeout=None)
    view.add_item(RematchButton(st.party.id))
    files = []
    if extras.equity_png:
        files.append(discord.File(io.BytesIO(extras.equity_png), filename="equity.png"))
        embed.set_image(url="attachment://equity.png")
    await channel.send(embed=embed, files=files, view=view)
