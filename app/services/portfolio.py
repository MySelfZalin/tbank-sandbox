import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.integrations.client import BrokerError, TInvestClient
from app.integrations.contracts import BrokerOrder, InstrumentData, Money, OperationData, timestamp
from app.models.entities import (
    Account,
    FundingRecord,
    Instrument,
    Operation,
    Order,
    PortfolioSnapshot,
)
from app.schemas.public import InstrumentSearchOut
from app.services.snapshots import build_snapshot, operation_values

logger = logging.getLogger(__name__)

Record = TypeVar("Record", Order, Operation, PortfolioSnapshot, FundingRecord)

TERMINAL = {
    "EXECUTION_REPORT_STATUS_FILL",
    "EXECUTION_REPORT_STATUS_REJECTED",
    "EXECUTION_REPORT_STATUS_CANCELLED",
}
PENDING = {"submitting", "unknown", "active", "reconciliation_required"}


class PortfolioService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], broker: TInvestClient):
        self.sessions = sessions
        self.broker = broker
        self.locks: dict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def recover_interrupted_fundings(self) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(FundingRecord)
                .where(FundingRecord.state == "submitting")
                .values(state="unknown", error_code="processing_interrupted")
            )

    async def account(self, account_id: UUID) -> Account:
        async with self.sessions() as session:
            account = await session.get(Account, account_id)
        if account is None:
            raise AppError("account_not_found", "Счёт не найден", 404)
        return account

    async def create_account(self, name: str) -> Account:
        result = await self.broker.open_account(name)
        account = Account(external_id=result.account_id, name=name)
        async with self.sessions() as session, session.begin():
            session.add(account)
        return account

    async def accounts(self) -> list[Account]:
        async with self.sessions() as session:
            return list((await session.scalars(select(Account).order_by(Account.created_at))).all())

    async def fund(self, account_id: UUID, amount: Decimal) -> FundingRecord:
        account = await self.account(account_id)
        async with self.locks[account_id]:
            record = FundingRecord(account_id=account_id, amount=amount)
            async with self.sessions() as session, session.begin():
                session.add(record)
            try:
                result = await self.broker.fund(account.external_id, Money.from_decimal(amount))
            except BrokerError as error:
                async with self.sessions() as session, session.begin():
                    stored = await session.get(FundingRecord, record.id)
                    stored.state = "unknown" if error.ambiguous else "failed"
                    stored.error_code = error.code
                if not error.ambiguous:
                    raise
            else:
                async with self.sessions() as session, session.begin():
                    stored = await session.get(FundingRecord, record.id)
                    stored.state = "confirmed"
                    stored.balance = result.balance.decimal()
            async with self.sessions() as session:
                return await session.get(FundingRecord, record.id)

    async def save_instruments(self, data: list[InstrumentData]) -> None:
        if not data:
            return
        async with self.sessions() as session, session.begin():
            for item in data:
                values = {
                    "uid": item.uid,
                    "ticker": item.ticker,
                    "name": item.name,
                    "currency": item.currency.lower(),
                    "lot": item.lot,
                    "instrument_type": item.instrument_type,
                    "buy_available": item.buy_available_flag and item.api_trade_available_flag,
                    "sell_available": item.sell_available_flag and item.api_trade_available_flag,
                }
                statement = insert(Instrument).values(**values)
                await session.execute(
                    statement.on_conflict_do_update(index_elements=[Instrument.uid], set_=values)
                )

    async def search(self, query: str) -> list[InstrumentSearchOut]:
        candidates = {}
        for kind in ("INSTRUMENT_TYPE_SHARE", "INSTRUMENT_TYPE_ETF"):
            response = await self.broker.search(query, instrument_kind=kind)
            for short in response.instruments:
                if short.api_trade_available_flag:
                    candidates[short.uid] = short
        items = []
        for short in list(candidates.values())[:20]:
            item = (await self.broker.instrument(short.uid)).instrument
            if item.currency.lower() == "rub" and item.instrument_type in {"share", "etf"}:
                items.append(item)
        if not items:
            return []
        await self.save_instruments(items)
        async with self.sessions() as session:
            instruments = list(
                (
                    await session.scalars(
                        select(Instrument)
                        .where(Instrument.uid.in_([item.uid for item in items]))
                        .order_by(Instrument.ticker, Instrument.uid)
                    )
                ).all()
            )
        return await self.attach_quotes(instruments)

    async def attach_quotes(self, instruments: list[Instrument]) -> list[InstrumentSearchOut]:
        results = [InstrumentSearchOut.model_validate(item) for item in instruments]
        if not results:
            return results
        try:
            response = await self.broker.last_prices([item.uid for item in instruments])
        except BrokerError as error:
            logger.warning("instrument_quotes_failed code=%s", error.code)
            return [
                item.model_copy(update={"quote_status": "error", "quote_error_code": error.code})
                for item in results
            ]
        quotes = {item.instrument_uid: item for item in response.last_prices}
        for item in results:
            quote = quotes.get(item.uid)
            if quote and quote.price is not None and quote.time is not None:
                price = quote.price.decimal()
                if price > 0:
                    item.last_price = price
                    item.last_price_at = quote.time.astimezone(UTC)
                    item.quote_status = "available"
        return results

    async def order(self, order_id: UUID) -> Order:
        async with self.sessions() as session:
            order = await session.get(Order, order_id)
        if order is None:
            raise AppError("order_not_found", "Заявка не найдена", 404)
        return order

    async def existing_order(
        self, account_id: UUID, key: str, uid: UUID, direction: str, quantity: int
    ) -> Order | None:
        async with self.sessions() as session:
            order = await session.scalar(
                select(Order).where(Order.account_id == account_id, Order.idempotency_key == key)
            )
        if order and (order.instrument_uid, order.direction, order.quantity_lots) != (
            uid,
            direction,
            quantity,
        ):
            raise AppError(
                "idempotency_conflict", "Ключ уже использован с другими параметрами", 409
            )
        return order

    async def create_order(
        self, account_id: UUID, uid: UUID, direction: str, quantity: int, key: str
    ) -> Order:
        account = await self.account(account_id)
        async with self.locks[account_id]:
            existing = await self.existing_order(account_id, key, uid, direction, quantity)
            if existing:
                return existing
            instrument = (await self.broker.instrument(uid)).instrument
            if instrument.currency.lower() != "rub" or instrument.instrument_type not in {
                "share",
                "etf",
            }:
                raise AppError("unsupported_instrument", "Поддерживаются рублёвые акции и фонды")
            available = (
                instrument.buy_available_flag
                if direction == "buy"
                else instrument.sell_available_flag
            )
            if not instrument.api_trade_available_flag or not available:
                raise AppError("instrument_unavailable", "Инструмент недоступен для этой сделки")
            if direction == "sell":
                async with self.sessions() as session:
                    pending = await session.scalar(
                        select(Order.id)
                        .where(
                            Order.account_id == account_id,
                            Order.instrument_uid == uid,
                            Order.processing_state.in_(PENDING),
                        )
                        .limit(1)
                    )
                if pending:
                    raise AppError(
                        "pending_order", "Сначала проверьте состояние незавершённой заявки", 409
                    )
                balances = await self.broker.positions(account.external_id)
                security = next((s for s in balances.securities if s.instrument_uid == uid), None)
                if balances.limits_loading_in_progress:
                    raise AppError("limits_loading", "Лимиты ещё загружаются, повторите позже", 409)
                if security is None or security.balance < quantity * instrument.lot:
                    raise AppError(
                        "insufficient_position", "Недостаточно свободных единиц инструмента", 409
                    )
            await self.save_instruments([instrument])
            order = Order(
                account_id=account_id,
                instrument_uid=uid,
                direction=direction,
                quantity_lots=quantity,
                idempotency_key=key,
            )
            try:
                async with self.sessions() as session, session.begin():
                    session.add(order)
            except IntegrityError:
                existing = await self.existing_order(account_id, key, uid, direction, quantity)
                if existing:
                    return existing
                raise
            try:
                result = await self.broker.post_order(
                    account.external_id, uid, quantity, direction, order.broker_request_id
                )
            except BrokerError as error:
                await self.record_order_error(order.id, error)
            else:
                await self.record_order_result(order.id, result)
            return await self.order(order.id)

    async def record_order_error(self, order_id: UUID, error: BrokerError) -> None:
        async with self.sessions() as session, session.begin():
            order = await session.get(Order, order_id)
            order.processing_state = "unknown" if error.ambiguous else "failed"
            order.error_code = error.code
            order.error_message = (
                "Результат отправки неизвестен; выполняется сверка без повторной отправки"
                if error.ambiguous
                else "Не удалось отправить заявку в T-Invest"
            )

    async def record_order_result(self, order_id: UUID, result: BrokerOrder) -> None:
        async with self.sessions() as session, session.begin():
            order = await session.get(Order, order_id)
            order.external_id = result.order_id
            order.broker_status = result.execution_report_status
            order.lots_executed = result.lots_executed
            order.processing_state = (
                "finished" if result.execution_report_status in TERMINAL else "active"
            )
            order.error_code = None
            order.error_message = None

    async def refresh_order(self, order_id: UUID) -> Order:
        order = await self.order(order_id)
        async with self.locks[order.account_id]:
            order = await self.order(order_id)
            if order.processing_state not in PENDING:
                return order
            account = await self.account(order.account_id)
            try:
                result = await self.broker.order_state(account.external_id, order.broker_request_id)
            except BrokerError as error:
                async with self.sessions() as session, session.begin():
                    stored = await session.get(Order, order_id)
                    stored.error_code = error.code
                    stored.error_message = (
                        "Не удалось сверить состояние; повторная отправка запрещена"
                    )
                    expired = datetime.now(UTC) - order.created_at > timedelta(days=7)
                    if error.status in {400, 404} or expired:
                        stored.processing_state = "reconciliation_required"
            else:
                await self.record_order_result(order_id, result)
            return await self.order(order_id)

    async def pending_order_ids(self, account_id: UUID | None = None) -> list[UUID]:
        query = (
            select(Order.id).where(Order.processing_state.in_(PENDING)).order_by(Order.created_at)
        )
        if account_id:
            query = query.where(Order.account_id == account_id)
        async with self.sessions() as session:
            return list((await session.scalars(query)).all())

    async def fetch_operations(self, account: Account) -> dict[str, OperationData]:
        end = datetime.now(UTC)
        start = account.created_at
        operations: dict[str, OperationData] = {}
        while start < end:
            window_end = min(start + timedelta(days=30), end)
            cursor = ""
            seen = set()
            while True:
                page = await self.broker.operations(
                    {
                        "accountId": account.external_id,
                        "from": timestamp(start),
                        "to": timestamp(window_end),
                        "limit": 100,
                        "cursor": cursor,
                    }
                )
                operations.update({item.id: item for item in page.items})
                if not page.has_next:
                    break
                if not page.next_cursor or page.next_cursor in seen:
                    raise AppError(
                        "broker_pagination", "Внешний API вернул некорректный курсор", 502
                    )
                seen.add(page.next_cursor)
                cursor = page.next_cursor
            start = window_end
        return operations

    async def sync(self, account_id: UUID) -> PortfolioSnapshot:
        account = await self.account(account_id)
        for order_id in await self.pending_order_ids(account_id):
            await self.refresh_order(order_id)
        async with self.locks[account_id]:
            operations = await self.fetch_operations(account)
            portfolio = await self.broker.portfolio(account.external_id)
            balances = await self.broker.positions(account.external_id)
            if balances.limits_loading_in_progress:
                raise AppError(
                    "limits_loading", "Лимиты ещё загружаются, повторите синхронизацию", 409
                )
            uids = {p.instrument_uid for p in portfolio.positions}
            uids.update(item.instrument_uid for item in operations.values() if item.instrument_uid)
            async with self.sessions() as session:
                known = set(
                    (
                        await session.scalars(
                            select(Instrument.uid).where(Instrument.uid.in_(uids))
                        )
                    ).all()
                )
            details = [(await self.broker.instrument(uid)).instrument for uid in uids - known]
            await self.save_instruments(details)
            snapshot = build_snapshot(account_id, portfolio, balances)
            async with self.sessions() as session, session.begin():
                for item in operations.values():
                    values = operation_values(account_id, item)
                    statement = insert(Operation).values(**values)
                    await session.execute(
                        statement.on_conflict_do_update(
                            index_elements=[Operation.account_id, Operation.external_id],
                            set_=values,
                        )
                    )
                session.add(snapshot)
            return snapshot

    async def list_records(
        self,
        model: type[Record],
        account_id: UUID,
        limit: int,
        offset: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Record]:
        await self.account(account_id)
        date_column = {
            Order: Order.created_at,
            Operation: Operation.date,
            PortfolioSnapshot: PortfolioSnapshot.synced_at,
            FundingRecord: FundingRecord.created_at,
        }[model]
        tie = Operation.external_id if model is Operation else model.id
        query = select(model).where(model.account_id == account_id)
        if start:
            query = query.where(date_column >= start)
        if end:
            query = query.where(date_column <= end)
        async with self.sessions() as session:
            return list(
                (
                    await session.scalars(
                        query.order_by(date_column.desc(), tie.desc()).limit(limit).offset(offset)
                    )
                ).all()
            )

    async def latest_portfolio(self, account_id: UUID) -> PortfolioSnapshot:
        snapshots = await self.list_records(PortfolioSnapshot, account_id, 1, 0)
        if not snapshots:
            raise AppError("portfolio_not_synced", "Сначала выполните синхронизацию", 404)
        return snapshots[0]
