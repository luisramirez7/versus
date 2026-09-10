"""Round timers, board cadence, snapshots, and what the poller should track."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from versus.db import LeaderboardSnapshot
from versus.engine.settlement import settle_round
from versus.engine.valuation import build_price_lookup, held_symbols, standings

from .embeds import live_message, recap_embed
from .recap import post_recap

if TYPE_CHECKING:
    from .main import VersusBot

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, bot: VersusBot) -> None:
        self.bot = bot
        self._task: asyncio.Task | None = None
        self._last_board: dict[int, datetime] = {}
        self._last_snapshot: dict[int, datetime] = {}
        self._warned: set[int] = set()
        self._live_ids: set[int] = set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="scheduler")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def market_active(self) -> bool:
        """The poller runs only while a live round exists and the market is open."""
        return bool(self._live_ids) and self.bot.svc.calendar.is_open(datetime.now(UTC))

    async def run(self) -> None:
        svc = self.bot.svc
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(svc.settings.scheduler_tick_s)

    async def tick(self) -> None:
        svc = self.bot.svc
        now = datetime.now(UTC)
        to_live, to_settle = await svc.rounds.due(now)

        for rnd in to_live:
            if await svc.rounds.mark_live(rnd.id):
                st = await svc.rounds.state(rnd.party_id)
                channel = await self._channel(st.party.channel_id)
                if channel:
                    await channel.send(live_message(st))
                await self.bot.board.refresh(rnd.party_id, repost=True)

        for rnd in to_settle:
            st = await svc.rounds.state(rnd.party_id)
            result = await settle_round(svc.db, svc.prices, rnd.id, now)
            if result is None:
                continue
            channel = await self._channel(st.party.channel_id)
            if channel:
                st = await svc.rounds.state(rnd.party_id)
                try:
                    await post_recap(self.bot, st, result, channel)
                except Exception:
                    log.exception("rich recap failed; posting the plain one")
                    await channel.send(embed=recap_embed(st, result))
            await self.bot.board.unpin(rnd.party_id)
            self._warned.discard(rnd.id)

        live = await svc.rounds.live_rounds()
        self._live_ids = {r.id for r in live}
        if not live:
            return

        symbols = await held_symbols(svc.db, [r.id for r in live])
        svc.prices.track(symbols)

        in_session = svc.calendar.is_open(now)
        for rnd in live:
            # last-hour notice for day sprints
            if (
                rnd.sessions == 1
                and rnd.end_at
                and rnd.id not in self._warned
                and now >= rnd.end_at - timedelta(hours=1)
            ):
                self._warned.add(rnd.id)
                st = await svc.rounds.state(rnd.party_id)
                channel = await self._channel(st.party.channel_id)
                if channel:
                    await channel.send("⏱ One hour left.")
            if not in_session:
                continue
            last = self._last_board.get(rnd.party_id)
            if last is None or now - last >= timedelta(seconds=svc.settings.board_refresh_s):
                self._last_board[rnd.party_id] = now
                self.bot.board.mark_dirty(rnd.party_id)
            last_snap = self._last_snapshot.get(rnd.id)
            if last_snap is None or now - last_snap >= timedelta(seconds=60):
                self._last_snapshot[rnd.id] = now
                await self._snapshot(rnd.id, now)

    async def _snapshot(self, round_id: int, now: datetime) -> None:
        svc = self.bot.svc
        symbols = await held_symbols(svc.db, [round_id])
        lookup = await build_price_lookup(svc.db, svc.prices, symbols)
        board = await standings(svc.db, lookup, round_id)
        async with svc.db.session() as s:
            for st in board:
                s.add(
                    LeaderboardSnapshot(
                        round_id=round_id,
                        portfolio_id=st.portfolio_id,
                        ts=now,
                        equity=st.equity,
                        cash=st.cash,
                        rank=st.rank,
                    )
                )
            await s.commit()

    async def _channel(self, channel_id: int):
        ch = self.bot.get_channel(channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(channel_id)
            except Exception:  # noqa: BLE001
                log.warning("channel %s unavailable", channel_id)
                return None
        return ch
