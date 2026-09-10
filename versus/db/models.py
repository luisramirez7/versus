"""SQLAlchemy models.

Money and quantities are stored as scaled integers (Fixed types) so they are exact on
SQLite and Postgres alike: money at 4 decimal places, shares at 6. Timestamps are stored
as naive UTC and returned timezone-aware.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class Fixed(TypeDecorator):
    """Decimal with a fixed scale, stored as a 64-bit integer."""

    impl = BigInteger
    cache_ok = True

    def __init__(self, scale: int) -> None:
        super().__init__()
        self.scale = scale
        self._quantum = Decimal(1).scaleb(-scale)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return int(Decimal(value).quantize(self._quantum, rounding=ROUND_HALF_EVEN).scaleb(self.scale))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value).scaleb(-self.scale)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; pass an aware UTC datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


Money = Fixed(4)
Shares = Fixed(6)


class Base(DeclarativeBase):
    pass


class Party(Base):
    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    host_user_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        String(16), default="lobby"
    )  # lobby|scheduled|live|settling|settled|cancelled
    lobby_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    board_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)

    rounds: Mapped[list[Round]] = relationship(back_populates="party")


class Round(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), index=True)
    preset: Mapped[str] = mapped_column(String(16))
    sessions: Mapped[int] = mapped_column(Integer)
    starting_cash: Mapped[Decimal] = mapped_column(Money)
    spread_bps: Mapped[int] = mapped_column(Integer, default=5)
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="lobby")
    settlement_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    party: Mapped[Party] = relationship(back_populates="rounds")
    portfolios: Mapped[list[Portfolio]] = relationship(back_populates="round")


class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("round_id", "owner_type", "owner_id", name="uq_portfolio_owner"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    owner_type: Mapped[str] = mapped_column(String(8), default="user")  # user|team
    owner_id: Mapped[int] = mapped_column(BigInteger)  # discord user id (or team id later)
    display_name: Mapped[str] = mapped_column(String(100))
    cash: Mapped[Decimal] = mapped_column(Money)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime)

    round: Mapped[Round] = relationship(back_populates="portfolios")
    positions: Mapped[list[Position]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")


class Position(Base):
    __tablename__ = "positions"

    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    qty: Mapped[Decimal] = mapped_column(Shares)
    avg_cost: Mapped[Decimal] = mapped_column(Money)

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))  # buy|sell
    req_shares: Mapped[Decimal | None] = mapped_column(Shares, nullable=True)
    req_dollars: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    status: Mapped[str] = mapped_column(String(10))  # pending|filled|rejected
    reject_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    interaction_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)

    fill: Mapped[Fill | None] = relationship(back_populates="order", uselist=False)


class Fill(Base):
    """Append-only. Records the exact quote used, so any result can be audited."""

    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))
    price: Mapped[Decimal] = mapped_column(Money)
    qty: Mapped[Decimal] = mapped_column(Shares)
    notional: Mapped[Decimal] = mapped_column(Money)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    quote_price: Mapped[Decimal] = mapped_column(Money)
    quoted_at: Mapped[datetime] = mapped_column(UTCDateTime)
    filled_at: Mapped[datetime] = mapped_column(UTCDateTime)

    order: Mapped[Order] = relationship(back_populates="fill")


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    price: Mapped[Decimal] = mapped_column(Money)


class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshots"

    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    equity: Mapped[Decimal] = mapped_column(Money)
    cash: Mapped[Decimal] = mapped_column(Money)
    rank: Mapped[int] = mapped_column(Integer)


class Instrument(Base):
    """Eligibility cache. Refreshed from the FMP profile endpoint about once a day."""

    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    exchange: Mapped[str] = mapped_column(String(16))
    is_etf: Mapped[bool] = mapped_column(Boolean, default=False)
    price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    market_cap: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    avg_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime)


Index("ix_fills_symbol", Fill.symbol)


class FeedMessage(Base):
    """The public feed line posted for a fill, so reactions can be tallied at settlement."""

    __tablename__ = "feed_messages"

    fill_id: Mapped[int] = mapped_column(ForeignKey("fills.id"), primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
