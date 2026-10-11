from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PublicModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["Учебный портфель"])


class AccountOut(PublicModel):
    id: UUID
    external_id: str
    name: str
    created_at: datetime


class FundCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=20, decimal_places=9, examples=["100000"])


class FundingOut(PublicModel):
    id: UUID
    account_id: UUID
    amount: Decimal
    state: str
    balance: Decimal | None
    error_code: str | None
    created_at: datetime


class InstrumentOut(PublicModel):
    uid: UUID
    ticker: str
    name: str
    currency: str
    lot: int
    instrument_type: str
    buy_available: bool
    sell_available: bool


class InstrumentSearchOut(InstrumentOut):
    last_price: Decimal | None = Field(default=None, description="Цена за единицу, не за лот")
    last_price_at: datetime | None = Field(default=None, description="Время последней цены в UTC")
    quote_status: Literal["available", "not_available", "error"] = "not_available"
    quote_error_code: str | None = None


class OrderCreate(BaseModel):
    instrument_uid: UUID
    direction: Literal["buy", "sell"]
    quantity_lots: Annotated[int, Field(strict=True, gt=0, le=1000000)]


class OrderOut(PublicModel):
    id: UUID
    account_id: UUID
    instrument_uid: UUID
    direction: str
    quantity_lots: int
    idempotency_key: str
    broker_request_id: UUID
    external_id: str | None
    processing_state: str
    broker_status: str | None
    lots_executed: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class OperationOut(PublicModel):
    external_id: str
    instrument_uid: UUID | None
    operation_type: str
    state: str
    date: datetime
    quantity_units: int
    quantity_done_units: int
    payment: Decimal
    currency: str
    price: Decimal
    commission: Decimal
    parent_operation_id: str | None
    name: str


class PositionOut(PublicModel):
    instrument_uid: UUID
    quantity_units: Decimal
    current_price: Decimal
    average_price: Decimal
    accrued_interest: Decimal
    value: Decimal
    currency: str


class PortfolioOut(PublicModel):
    id: UUID
    account_id: UUID
    synced_at: datetime
    total_value: Decimal
    currency_value: Decimal
    shares_value: Decimal
    bonds_value: Decimal
    funds_value: Decimal
    cash: Decimal
    blocked_cash: Decimal
    positions: list[PositionOut]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
