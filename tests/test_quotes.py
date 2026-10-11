import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.client import BASE_URL, TInvestClient
from app.models.entities import Instrument
from app.services.portfolio import PortfolioService


def instrument(uid, ticker="TEST", lot=10):
    return Instrument(
        uid=uid,
        ticker=ticker,
        name="Учебный инструмент",
        currency="rub",
        lot=lot,
        instrument_type="share",
        buy_available=True,
        sell_available=True,
    )


async def test_quotes_are_batched_matched_by_uid_and_not_multiplied_by_lot():
    first, second = uuid4(), uuid4()
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["instrumentId"] == [str(first), str(second)]
        assert payload["lastPriceType"] == "LAST_PRICE_EXCHANGE"
        assert (
            str(request.url)
            == BASE_URL + "tinkoff.public.invest.api.contract.v1.MarketDataService/GetLastPrices"
        )
        return httpx.Response(
            200,
            json={
                "lastPrices": [
                    {
                        "instrumentUid": str(second),
                        "price": {"units": "100", "nano": 1},
                        "time": "2026-10-09T18:45:00+03:00",
                    },
                    {
                        "instrumentUid": str(first),
                        "price": {"units": "286", "nano": 200000001},
                        "time": "2026-10-09T15:40:00Z",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = PortfolioService(async_sessionmaker(), TInvestClient(http, "test-only"))
        results = await service.attach_quotes([instrument(first), instrument(second)])
    assert len(calls) == 1
    assert results[0].last_price == Decimal("286.200000001")
    assert results[1].last_price == Decimal("100.000000001")
    assert results[1].last_price_at.isoformat() == "2026-10-09T15:45:00+00:00"
    assert all(item.quote_status == "available" for item in results)
    assert results[0].lot == 10


@pytest.mark.parametrize(
    "quote",
    [
        None,
        {"price": None},
        {"price": {"units": "286", "nano": 0}, "time": None},
        {"price": {"units": "0", "nano": 0}},
        {"price": {"units": "-1", "nano": 0}},
    ],
)
async def test_missing_or_invalid_price_does_not_hide_instrument_or_invent_zero(quote):
    uid = uuid4()

    def handler(request):
        items = (
            []
            if quote is None
            else [{"instrumentUid": str(uid), "time": "2026-10-09T15:45:00Z"} | quote]
        )
        return httpx.Response(200, json={"lastPrices": items})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = PortfolioService(async_sessionmaker(), TInvestClient(http, "test-only"))
        result = (await service.attach_quotes([instrument(uid)]))[0]
    assert result.uid == uid
    assert result.quote_status == "not_available"
    assert result.last_price is None
    assert result.last_price_at is None
    assert result.quote_error_code is None


@pytest.mark.parametrize(
    "status, attempts, code", [(503, 2, "broker_request_failed"), (401, 1, "broker_auth")]
)
async def test_quote_error_is_explicit_and_keeps_search_results(status, attempts, code):
    uid = uuid4()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"message": "internal broker text"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = PortfolioService(async_sessionmaker(), TInvestClient(http, "test-only", 2))
        result = (await service.attach_quotes([instrument(uid)]))[0]
    assert len(calls) == attempts
    assert result.uid == uid
    assert result.quote_status == "error"
    assert result.quote_error_code == code
    assert result.last_price is None
    assert "internal broker text" not in result.model_dump_json()


async def test_empty_instrument_list_never_requests_all_market_prices():
    def handler(request):
        raise AssertionError("Empty list must not call broker")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        broker = TInvestClient(http, "test-only")
        service = PortfolioService(async_sessionmaker(), broker)
        assert await service.attach_quotes([]) == []
        assert (await broker.last_prices([])).last_prices == []
