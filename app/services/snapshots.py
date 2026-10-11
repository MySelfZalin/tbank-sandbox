from datetime import UTC, datetime
from decimal import Decimal, localcontext
from uuid import UUID

from app.integrations.contracts import OperationData, PortfolioData, PositionsData
from app.models.entities import PortfolioSnapshot, SnapshotPosition


def operation_values(account_id: UUID, item: OperationData) -> dict[str, object]:
    return {
        "account_id": account_id,
        "external_id": item.id,
        "instrument_uid": item.instrument_uid,
        "operation_type": item.type,
        "state": item.state,
        "date": item.date,
        "quantity_units": item.quantity,
        "quantity_done_units": item.quantity_done,
        "payment": item.payment.decimal(),
        "currency": item.payment.currency,
        "price": item.price.decimal(),
        "commission": item.commission.decimal(),
        "parent_operation_id": item.parent_operation_id or None,
        "name": item.name,
    }


def build_snapshot(
    account_id: UUID, portfolio: PortfolioData, balances: PositionsData
) -> PortfolioSnapshot:
    with localcontext() as context:
        context.prec = 60
        return PortfolioSnapshot(
            account_id=account_id,
            synced_at=datetime.now(UTC),
            total_value=portfolio.total_amount_portfolio.decimal(),
            currency_value=portfolio.total_amount_currencies.decimal(),
            shares_value=portfolio.total_amount_shares.decimal(),
            bonds_value=portfolio.total_amount_bonds.decimal(),
            funds_value=portfolio.total_amount_etf.decimal(),
            cash=sum((m.decimal() for m in balances.money if m.currency == "rub"), Decimal(0)),
            blocked_cash=sum(
                (m.decimal() for m in balances.blocked if m.currency == "rub"), Decimal(0)
            ),
            positions=[
                SnapshotPosition(
                    instrument_uid=p.instrument_uid,
                    quantity_units=p.quantity.decimal(),
                    current_price=p.current_price.decimal(),
                    average_price=p.average_position_price.decimal(),
                    accrued_interest=p.current_nkd.decimal(),
                    value=p.quantity.decimal()
                    * (p.current_price.decimal() + p.current_nkd.decimal()),
                    currency=p.current_price.currency,
                )
                for p in portfolio.positions
            ],
        )
