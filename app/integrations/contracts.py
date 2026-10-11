from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class ExternalModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Quotation(ExternalModel):
    units: int = 0
    nano: int = Field(default=0, ge=-999999999, le=999999999)

    def decimal(self) -> Decimal:
        return Decimal(self.units) + Decimal(self.nano) / Decimal(1000000000)

    @classmethod
    def from_decimal(cls, value: Decimal) -> "Quotation":
        units = int(value)
        return cls(units=units, nano=int((value - units) * Decimal(1000000000)))

    def wire(self) -> dict:
        return {"units": str(self.units), "nano": self.nano}


class Money(Quotation):
    currency: str = "rub"


class OpenAccount(ExternalModel):
    account_id: str = Field(min_length=1)


class Funding(ExternalModel):
    balance: Money


class InstrumentData(ExternalModel):
    uid: UUID
    ticker: str
    name: str
    currency: str
    lot: int = Field(gt=0)
    instrument_type: str
    api_trade_available_flag: bool = False
    buy_available_flag: bool = False
    sell_available_flag: bool = False


class InstrumentResponse(ExternalModel):
    instrument: InstrumentData


class InstrumentShort(ExternalModel):
    uid: UUID
    api_trade_available_flag: bool = False


class SearchResponse(ExternalModel):
    instruments: list[InstrumentShort] = Field(default_factory=list)


class LastPriceData(ExternalModel):
    instrument_uid: UUID
    price: Quotation | None = None
    time: AwareDatetime | None = None


class LastPricesResponse(ExternalModel):
    last_prices: list[LastPriceData] = Field(default_factory=list)


class BrokerOrder(ExternalModel):
    order_id: str = Field(min_length=1)
    execution_report_status: str
    lots_executed: int = 0


class OperationData(ExternalModel):
    id: str
    date: AwareDatetime
    type: str
    state: str
    instrument_uid: UUID | None = None
    quantity: int = 0
    quantity_done: int = 0
    payment: Money = Field(default_factory=Money)
    price: Money = Field(default_factory=Money)
    commission: Money = Field(default_factory=Money)
    parent_operation_id: str = ""
    name: str = ""

    @field_validator("instrument_uid", mode="before")
    @classmethod
    def empty_uid(cls, value):
        return value or None


class OperationsPage(ExternalModel):
    has_next: bool = False
    next_cursor: str = ""
    items: list[OperationData] = Field(default_factory=list)


class PositionData(ExternalModel):
    instrument_uid: UUID
    quantity: Quotation
    current_price: Money
    average_position_price: Money = Field(default_factory=Money)
    current_nkd: Money = Field(default_factory=Money)


class PortfolioData(ExternalModel):
    total_amount_portfolio: Money
    total_amount_currencies: Money = Field(default_factory=Money)
    total_amount_shares: Money = Field(default_factory=Money)
    total_amount_bonds: Money = Field(default_factory=Money)
    total_amount_etf: Money = Field(default_factory=Money)
    positions: list[PositionData] = Field(default_factory=list)


class SecurityBalance(ExternalModel):
    instrument_uid: UUID
    balance: int = 0
    blocked: int = 0


class PositionsData(ExternalModel):
    money: list[Money] = Field(default_factory=list)
    blocked: list[Money] = Field(default_factory=list)
    securities: list[SecurityBalance] = Field(default_factory=list)
    limits_loading_in_progress: bool = False


def timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
