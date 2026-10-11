import asyncio
import logging
import ssl
from time import monotonic
from typing import TypeVar
from uuid import UUID

import httpx

from app.integrations.contracts import (
    BrokerOrder,
    ExternalModel,
    Funding,
    InstrumentResponse,
    LastPricesResponse,
    Money,
    OpenAccount,
    OperationsPage,
    PortfolioData,
    PositionsData,
    SearchResponse,
)

BASE_URL = "https://sandbox-invest-public-api.tbank.ru/rest/"
PREFIX = "tinkoff.public.invest.api.contract.v1."
logger = logging.getLogger(__name__)
T = TypeVar("T", bound=ExternalModel)


def certificate_verification_failed(error: BaseException) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        current = current.__cause__ or current.__context__
    return False


class BrokerError(Exception):
    def __init__(self, code: str, ambiguous: bool = False, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.ambiguous = ambiguous
        self.status = status


class TInvestClient:
    def __init__(self, http: httpx.AsyncClient, token: str, read_attempts: int = 3):
        self.http = http
        self.token = token
        self.read_attempts = read_attempts

    async def call(
        self, service: str, method: str, payload: dict, model: type[T], *, read: bool = True
    ) -> T:
        if not self.token:
            raise BrokerError("broker_token_missing")
        attempts = self.read_attempts if read else 1
        for attempt in range(attempts):
            started = monotonic()
            status = None
            tracking = None
            transport_error = None
            try:
                response = await self.http.post(
                    BASE_URL + PREFIX + service + "/" + method,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                status = response.status_code
                tracking = response.headers.get("x-tracking-id")
                transient = status in {408, 429, 500, 502, 503, 504}
                if transient and read and attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * 2**attempt)
                    continue
                if status >= 400:
                    code = "broker_auth" if status in {401, 403} else "broker_request_failed"
                    raise BrokerError(
                        code,
                        ambiguous=(status >= 500 or status in {408, 429}) and not read,
                        status=status,
                    )
                try:
                    return model.model_validate(response.json())
                except ValueError as error:
                    raise BrokerError("broker_invalid_response", ambiguous=not read) from error
            except httpx.TransportError as error:
                transport_error = type(error).__name__
                if certificate_verification_failed(error):
                    transport_error = "SSLCertVerificationError"
                    raise BrokerError("broker_tls_verification_failed") from error
                if read and attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * 2**attempt)
                    continue
                connection_failed = isinstance(
                    error, (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout)
                )
                code = "broker_connection_failed" if connection_failed else "broker_unavailable"
                raise BrokerError(code, ambiguous=not read and not connection_failed) from error
            finally:
                logger.info(
                    "broker operation=%s duration_ms=%.1f status=%s "
                    "tracking_id=%s transport_error=%s",
                    method,
                    (monotonic() - started) * 1000,
                    status,
                    tracking,
                    transport_error,
                )
        raise BrokerError("broker_unavailable")

    async def sandbox(self, method: str, payload: dict, model: type[T], *, read: bool = True) -> T:
        return await self.call("SandboxService", method, payload, model, read=read)

    async def open_account(self, name: str) -> OpenAccount:
        return await self.sandbox("OpenSandboxAccount", {"name": name}, OpenAccount, read=False)

    async def fund(self, account: str, amount: Money) -> Funding:
        return await self.sandbox(
            "SandboxPayIn",
            {"accountId": account, "amount": amount.wire() | {"currency": "rub"}},
            Funding,
            read=False,
        )

    async def search(self, query: str, instrument_kind: str | None = None) -> SearchResponse:
        payload = {"query": query, "apiTradeAvailableFlag": True}
        if instrument_kind is not None:
            payload["instrumentKind"] = instrument_kind
        return await self.call("InstrumentsService", "FindInstrument", payload, SearchResponse)

    async def instrument(self, uid: UUID) -> InstrumentResponse:
        return await self.call(
            "InstrumentsService",
            "GetInstrumentBy",
            {"idType": "INSTRUMENT_ID_TYPE_UID", "id": str(uid)},
            InstrumentResponse,
        )

    async def last_prices(self, uids: list[UUID]) -> LastPricesResponse:
        if not uids:
            return LastPricesResponse()
        return await self.call(
            "MarketDataService",
            "GetLastPrices",
            {
                "instrumentId": [str(uid) for uid in uids],
                "lastPriceType": "LAST_PRICE_EXCHANGE",
            },
            LastPricesResponse,
        )

    async def post_order(
        self, account: str, uid: UUID, quantity: int, direction: str, request_id: UUID
    ) -> BrokerOrder:
        return await self.sandbox(
            "PostSandboxOrder",
            {
                "accountId": account,
                "instrumentId": str(uid),
                "quantity": str(quantity),
                "direction": f"ORDER_DIRECTION_{direction.upper()}",
                "orderType": "ORDER_TYPE_MARKET",
                "orderId": str(request_id),
            },
            BrokerOrder,
            read=False,
        )

    async def order_state(self, account: str, request_id: UUID) -> BrokerOrder:
        return await self.sandbox(
            "GetSandboxOrderState",
            {
                "accountId": account,
                "orderId": str(request_id),
                "orderIdType": "ORDER_ID_TYPE_REQUEST",
            },
            BrokerOrder,
        )

    async def operations(self, payload: dict) -> OperationsPage:
        return await self.sandbox("GetSandboxOperationsByCursor", payload, OperationsPage)

    async def portfolio(self, account: str) -> PortfolioData:
        return await self.sandbox(
            "GetSandboxPortfolio", {"accountId": account, "currency": "RUB"}, PortfolioData
        )

    async def positions(self, account: str) -> PositionsData:
        return await self.sandbox("GetSandboxPositions", {"accountId": account}, PositionsData)
