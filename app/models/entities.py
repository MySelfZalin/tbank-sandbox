from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def now() -> datetime:
    return datetime.now(UTC)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    external_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (CheckConstraint("lot > 0", name="instrument_positive_lot"),)
    uid: Mapped[UUID] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(300))
    currency: Mapped[str] = mapped_column(String(3))
    lot: Mapped[int]
    instrument_type: Mapped[str] = mapped_column(String(30))
    buy_available: Mapped[bool]
    sell_available: Mapped[bool]


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key", name="uq_order_account_key"),
        CheckConstraint("quantity_lots > 0", name="order_positive_lots"),
        CheckConstraint("direction IN ('buy', 'sell')", name="order_direction"),
        Index("ix_order_account_created", "account_id", "created_at"),
        Index("ix_order_processing", "processing_state"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    instrument_uid: Mapped[UUID] = mapped_column(ForeignKey("instruments.uid"))
    direction: Mapped[str] = mapped_column(String(4))
    quantity_lots: Mapped[int]
    idempotency_key: Mapped[str] = mapped_column(String(100))
    broker_request_id: Mapped[UUID] = mapped_column(unique=True, default=uuid4)
    external_id: Mapped[str | None] = mapped_column(String(100))
    processing_state: Mapped[str] = mapped_column(String(30), default="submitting")
    broker_status: Mapped[str | None] = mapped_column(String(60))
    lots_executed: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (Index("ix_operation_account_date", "account_id", "date"),)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    instrument_uid: Mapped[UUID | None] = mapped_column(ForeignKey("instruments.uid"))
    operation_type: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(60))
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quantity_units: Mapped[int] = mapped_column(BigInteger)
    quantity_done_units: Mapped[int] = mapped_column(BigInteger)
    payment: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    currency: Mapped[str] = mapped_column(String(3))
    price: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    commission: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    parent_operation_id: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (Index("ix_snapshot_account_time", "account_id", "synced_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    total_value: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    currency_value: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    shares_value: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    bonds_value: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    funds_value: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    cash: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    blocked_cash: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    positions: Mapped[list["SnapshotPosition"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class SnapshotPosition(Base):
    __tablename__ = "portfolio_snapshot_positions"
    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("portfolio_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    instrument_uid: Mapped[UUID] = mapped_column(ForeignKey("instruments.uid"), primary_key=True)
    quantity_units: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    current_price: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    average_price: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    accrued_interest: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    value: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    currency: Mapped[str] = mapped_column(String(3))


class FundingRecord(Base):
    __tablename__ = "fundings"
    __table_args__ = (Index("ix_funding_account_time", "account_id", "created_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(30, 9))
    state: Mapped[str] = mapped_column(String(30), default="submitting")
    balance: Mapped[Decimal | None] = mapped_column(Numeric(30, 9))
    error_code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
