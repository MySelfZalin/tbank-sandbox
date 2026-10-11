from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import AwareDatetime

from app.core.errors import AppError
from app.db.session import database_is_ready
from app.models.entities import (
    Account,
    FundingRecord,
    Operation,
    Order,
    PortfolioSnapshot,
)
from app.schemas.public import (
    AccountCreate,
    AccountOut,
    ErrorResponse,
    FundCreate,
    FundingOut,
    InstrumentSearchOut,
    OperationOut,
    OrderCreate,
    OrderOut,
    PortfolioOut,
)
from app.services.portfolio import PortfolioService

router = APIRouter(
    responses={status: {"model": ErrorResponse} for status in (400, 404, 409, 422, 500, 502, 503)}
)


def service(request: Request) -> PortfolioService:
    return request.app.state.service


Service = Annotated[PortfolioService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


@router.post(
    "/accounts",
    response_model=AccountOut,
    status_code=201,
    summary="Создать виртуальный счёт песочницы",
)
async def create_account(body: AccountCreate, svc: Service) -> Account:
    return await svc.create_account(body.name)


@router.get("/accounts", response_model=list[AccountOut])
async def accounts(svc: Service) -> list[Account]:
    return await svc.accounts()


@router.post(
    "/accounts/{account_id}/fund",
    response_model=FundingOut,
    summary="Пополнить виртуальными рублями; автоматических повторов нет",
)
async def fund(account_id: UUID, body: FundCreate, svc: Service) -> FundingRecord:
    return await svc.fund(account_id, body.amount)


@router.get(
    "/accounts/{account_id}/fundings",
    response_model=list[FundingOut],
    summary="Локальный журнал виртуальных пополнений, новые сначала",
)
async def fundings(
    account_id: UUID, svc: Service, limit: Limit = 20, offset: Offset = 0
) -> list[FundingRecord]:
    return await svc.list_records(FundingRecord, account_id, limit, offset)


@router.get(
    "/instruments/search",
    response_model=list[InstrumentSearchOut],
    summary="Поиск рублёвых акций и фондов с последней биржевой ценой; максимум 20 кандидатов",
    description=(
        "last_price — цена за единицу инструмента, не за лот. "
        "Вне торгов цена может быть старой; её время указано в last_price_at. "
        "quote_status: available, not_available или error. "
        "Ошибка котировок не скрывает найденные инструменты."
    ),
)
async def instruments(
    svc: Service, query: Annotated[str, Query(min_length=1, max_length=100)]
) -> list[InstrumentSearchOut]:
    return await svc.search(query)


@router.post(
    "/accounts/{account_id}/orders",
    response_model=OrderOut,
    summary="Виртуальная рыночная заявка; quantity_lots — целое число лотов",
)
async def create_order(
    account_id: UUID,
    body: OrderCreate,
    svc: Service,
    idempotency_key: Annotated[
        str, Header(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    ],
) -> Order:
    return await svc.create_order(
        account_id, body.instrument_uid, body.direction, body.quantity_lots, idempotency_key
    )


@router.get(
    "/orders/{order_id}",
    response_model=OrderOut,
    summary="Локальная заявка; refresh=true сверяет состояние у брокера",
)
async def order(order_id: UUID, svc: Service, refresh: bool = False) -> Order:
    return await svc.refresh_order(order_id) if refresh else await svc.order(order_id)


@router.get(
    "/accounts/{account_id}/orders", response_model=list[OrderOut], summary="Заявки, новые сначала"
)
async def orders(
    account_id: UUID, svc: Service, limit: Limit = 20, offset: Offset = 0
) -> list[Order]:
    return await svc.list_records(Order, account_id, limit, offset)


@router.post(
    "/accounts/{account_id}/sync",
    response_model=PortfolioOut,
    summary="Сверить заявки, все страницы операций и портфель песочницы",
)
async def sync(account_id: UUID, svc: Service) -> PortfolioSnapshot:
    return await svc.sync(account_id)


@router.get(
    "/accounts/{account_id}/operations",
    response_model=list[OperationOut],
    summary="Операции брокера, новые сначала; количество в единицах инструмента",
)
async def operations(
    account_id: UUID, svc: Service, limit: Limit = 20, offset: Offset = 0
) -> list[Operation]:
    return await svc.list_records(Operation, account_id, limit, offset)


@router.get(
    "/accounts/{account_id}/portfolio",
    response_model=PortfolioOut,
    summary="Последний сохранённый портфель",
)
async def portfolio(account_id: UUID, svc: Service) -> PortfolioSnapshot:
    return await svc.latest_portfolio(account_id)


@router.get(
    "/accounts/{account_id}/portfolio/history",
    response_model=list[PortfolioOut],
    summary="История стоимости, новые сначала; изменение стоимости не равно доходности",
)
async def history(
    account_id: UUID,
    svc: Service,
    limit: Limit = 20,
    offset: Offset = 0,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
) -> list[PortfolioSnapshot]:
    if start and end and start > end:
        raise AppError("invalid_period", "Начало периода должно предшествовать окончанию")
    return await svc.list_records(PortfolioSnapshot, account_id, limit, offset, start, end)


@router.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", tags=["health"])
async def ready(request: Request) -> dict[str, str]:
    if not await database_is_ready(request.app.state.sessions):
        raise AppError("migrations_pending", "Примените миграции базы данных", 503)
    if not request.app.state.settings.tinvest_token.get_secret_value():
        raise AppError("broker_token_missing", "TINVEST_TOKEN не настроен", 503)
    task = request.app.state.worker
    if task.done():
        raise AppError("worker_unavailable", "Проверка заявок остановлена", 503)
    return {"status": "ready"}
