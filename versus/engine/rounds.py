"""Party and round lifecycle: lobby → scheduled → live → settling → settled."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from versus.db import Database, Party, Portfolio, Round
from versus.market.clock import MarketCalendar

from .rules import CASH_OPTIONS, PRESETS, Rules

OPEN_STATUSES = ("lobby", "scheduled", "live", "settling")


class RoundError(Exception):
    """User-facing."""


@dataclass(frozen=True)
class PartyState:
    party: Party
    round: Round
    members: list[Portfolio]

    @property
    def rules(self) -> Rules:
        return rules_for(self.round)


def rules_for(rnd: Round) -> Rules:
    return Rules(starting_cash=rnd.starting_cash, preset=rnd.preset, spread_bps=rnd.spread_bps)


class RoundService:
    def __init__(
        self,
        db: Database,
        calendar: MarketCalendar,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        min_players: int = 2,
    ):
        self.db = db
        self.calendar = calendar
        self.clock = clock
        self.min_players = min_players

    # ----- lookups -----
    async def active(self, channel_id: int) -> PartyState | None:
        async with self.db.session() as s:
            party = (
                await s.execute(
                    select(Party)
                    .where(Party.channel_id == channel_id, Party.status.in_(OPEN_STATUSES))
                    .order_by(Party.id.desc())
                )
            ).scalar_one_or_none()
            if party is None:
                return None
            rnd = (
                await s.execute(select(Round).where(Round.party_id == party.id).order_by(Round.id.desc()))
            ).scalar_one()
            members = (
                (
                    await s.execute(
                        select(Portfolio).where(Portfolio.round_id == rnd.id).order_by(Portfolio.joined_at)
                    )
                )
                .scalars()
                .all()
            )
            return PartyState(party, rnd, list(members))

    async def state(self, party_id: int) -> PartyState:
        async with self.db.session() as s:
            party = await s.get(Party, party_id)
            if party is None:
                raise RoundError("No such party.")
            rnd = (
                await s.execute(select(Round).where(Round.party_id == party.id).order_by(Round.id.desc()))
            ).scalar_one()
            members = (
                (
                    await s.execute(
                        select(Portfolio).where(Portfolio.round_id == rnd.id).order_by(Portfolio.joined_at)
                    )
                )
                .scalars()
                .all()
            )
            return PartyState(party, rnd, list(members))

    async def portfolio_for(self, round_id: int, user_id: int) -> Portfolio | None:
        async with self.db.session() as s:
            return (
                await s.execute(
                    select(Portfolio).where(
                        Portfolio.round_id == round_id,
                        Portfolio.owner_type == "user",
                        Portfolio.owner_id == user_id,
                    )
                )
            ).scalar_one_or_none()

    # ----- lifecycle -----
    async def create(
        self, *, guild_id: int, channel_id: int, host_user_id: int, cash: int, preset: str
    ) -> PartyState:
        if cash not in CASH_OPTIONS:
            raise RoundError(f"Starting cash must be one of {', '.join(f'${c:,}' for c in CASH_OPTIONS)}.")
        if preset not in PRESETS:
            raise RoundError(f"Preset must be one of {', '.join(PRESETS)}.")
        if await self.active(channel_id):
            raise RoundError(
                "This channel already has a party. `/party status` to see it, or wait for it to settle."
            )
        now = self.clock()
        async with self.db.session() as s:
            party = Party(
                guild_id=guild_id,
                channel_id=channel_id,
                host_user_id=host_user_id,
                status="lobby",
                created_at=now,
            )
            s.add(party)
            await s.flush()
            rnd = Round(
                party_id=party.id,
                preset=preset,
                sessions=PRESETS[preset],
                starting_cash=Decimal(cash),
                spread_bps=Rules().spread_bps,
                status="lobby",
            )
            s.add(rnd)
            await s.commit()
            return PartyState(party, rnd, [])

    async def join(self, party_id: int, user_id: int, display_name: str) -> Portfolio:
        st = await self.state(party_id)
        if st.party.status not in ("lobby", "scheduled"):
            raise RoundError("This round is already live. You can join the next one.")
        if any(m.owner_id == user_id for m in st.members):
            raise RoundError("You're already in.")
        async with self.db.session() as s:
            pf = Portfolio(
                round_id=st.round.id,
                owner_type="user",
                owner_id=user_id,
                display_name=display_name[:100],
                cash=st.round.starting_cash,
                joined_at=self.clock(),
            )
            s.add(pf)
            await s.commit()
            return pf

    async def leave(self, party_id: int, user_id: int) -> None:
        st = await self.state(party_id)
        if st.party.status not in ("lobby", "scheduled"):
            raise RoundError("You can't leave a live round. Ride it out.")
        async with self.db.session() as s:
            pf = await self.portfolio_for(st.round.id, user_id)
            if pf is None:
                raise RoundError("You're not in this party.")
            await s.delete(await s.get(Portfolio, pf.id))
            await s.commit()

    async def start(self, party_id: int, user_id: int) -> PartyState:
        st = await self.state(party_id)
        if st.party.host_user_id != user_id:
            raise RoundError("Only the host can start the round.")
        if st.party.status != "lobby":
            raise RoundError("The round has already been started.")
        if len(st.members) < self.min_players:
            raise RoundError(f"Need at least {self.min_players} players. Share `/join` with someone.")
        now = self.clock()
        start_at, end_at = self.calendar.round_window(now, st.round.sessions)
        status = "live" if start_at <= now else "scheduled"
        async with self.db.session() as s:
            party = await s.get(Party, party_id)
            rnd = await s.get(Round, st.round.id)
            rnd.start_at, rnd.end_at, rnd.status = start_at, end_at, status
            party.status = status
            await s.commit()
        return await self.state(party_id)

    async def cancel(self, party_id: int, user_id: int) -> None:
        st = await self.state(party_id)
        if st.party.host_user_id != user_id:
            raise RoundError("Only the host can cancel.")
        if st.party.status not in ("lobby", "scheduled"):
            raise RoundError("A live round can't be cancelled. `/party end` settles it now.")
        async with self.db.session() as s:
            (await s.get(Party, party_id)).status = "cancelled"
            (await s.get(Round, st.round.id)).status = "cancelled"
            await s.commit()

    async def end_now(self, party_id: int, user_id: int) -> PartyState:
        st = await self.state(party_id)
        if st.party.host_user_id != user_id:
            raise RoundError("Only the host can end the round.")
        if st.party.status != "live":
            raise RoundError("The round isn't live.")
        async with self.db.session() as s:
            (await s.get(Round, st.round.id)).end_at = self.clock()
            await s.commit()
        return await self.state(party_id)

    async def mark_live(self, round_id: int) -> bool:
        async with self.db.session() as s:
            rnd = await s.get(Round, round_id)
            if rnd is None or rnd.status != "scheduled":
                return False
            rnd.status = "live"
            (await s.get(Party, rnd.party_id)).status = "live"
            await s.commit()
            return True

    async def due(self, now: datetime | None = None) -> tuple[list[Round], list[Round]]:
        """(rounds to go live, rounds to settle)."""
        now = now or self.clock()
        async with self.db.session() as s:
            to_live = (
                (await s.execute(select(Round).where(Round.status == "scheduled", Round.start_at <= now)))
                .scalars()
                .all()
            )
            to_settle = (
                (await s.execute(select(Round).where(Round.status == "live", Round.end_at <= now)))
                .scalars()
                .all()
            )
            return list(to_live), list(to_settle)

    async def live_rounds(self) -> list[Round]:
        async with self.db.session() as s:
            return list((await s.execute(select(Round).where(Round.status == "live"))).scalars().all())

    async def set_messages(
        self, party_id: int, *, lobby_message_id: int | None = None, board_message_id: int | None = None
    ):
        async with self.db.session() as s:
            party = await s.get(Party, party_id)
            if lobby_message_id is not None:
                party.lobby_message_id = lobby_message_id
            if board_message_id is not None:
                party.board_message_id = board_message_id
            await s.commit()
