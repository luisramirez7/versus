from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from versus.config import Settings, settings

from .board import BoardManager
from .cogs.info import InfoCog
from .cogs.party import JoinView, PartyCog
from .cogs.trade import TradeCog
from .recap import RematchButton
from .scheduler import Scheduler
from .services import Services

log = logging.getLogger(__name__)

# View Channel, Send Messages, Manage Messages (pin), Embed Links, Read Message History
INVITE_PERMISSIONS = 1024 | 2048 | 8192 | 16384 | 65536


def invite_url(application_id: int) -> str:
    return (
        "https://discord.com/oauth2/authorize"
        f"?client_id={application_id}&scope=bot%20applications.commands&permissions={INVITE_PERMISSIONS}"
    )


class VersusBot(commands.Bot):
    def __init__(self, svc: Services) -> None:
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.svc = svc
        self.board = BoardManager(self)
        self.scheduler = Scheduler(self)

    async def setup_hook(self) -> None:
        await self.add_cog(PartyCog(self))
        await self.add_cog(TradeCog(self))
        await self.add_cog(InfoCog(self))
        self.add_view(JoinView(self))
        self.add_dynamic_items(RematchButton)
        log.info("invite link: %s", invite_url(self.application_id))
        try:
            if self.svc.settings.dev_guild_id:
                guild = discord.Object(id=self.svc.settings.dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("synced %d commands to dev guild %s", len(synced), guild.id)
            else:
                synced = await self.tree.sync()
                log.info("synced %d global commands (may take up to an hour to appear)", len(synced))
        except discord.Forbidden:
            log.error(
                "Discord refused to register commands (Missing Access). The bot is not in guild %s, or was invited "
                "without the applications.commands scope. Invite it with the link above, then restart.",
                self.svc.settings.dev_guild_id,
            )
        self.svc.prices.start(self.scheduler.market_active)
        self.scheduler.start()

    async def on_ready(self) -> None:
        log.info("logged in as %s (%s) in %d guilds", self.user, self.user.id, len(self.guilds))

    async def close(self) -> None:
        self.scheduler.stop()
        await self.svc.close()
        await super().close()


async def run_bot(cfg: Settings = settings) -> None:
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines include the FMP key in the URL
    if not cfg.discord_token:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    svc = await Services.build(cfg)
    bot = VersusBot(svc)
    async with bot:
        await bot.start(cfg.discord_token)


def main() -> None:
    asyncio.run(run_bot())
