import ssl
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.integrations.client import BASE_URL, BrokerError, TInvestClient
from app.integrations.contracts import Money, Quotation
from app.schemas.public import FundCreate, OrderCreate


@pytest.mark.parametrize("value", ["123.000000001", "-12.999999999", "-0.000000001", "0", "100000"])
def test_money_exact_round_trip(value):
    amount = Decimal(value)
    quote = Quotation.from_decimal(amount)
    assert quote.decimal() == amount
    assert Quotation.model_validate(quote.wire()).decimal() == amount


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "1", True])
def test_lots_are_positive_strict_integers(quantity):
    with pytest.raises(ValidationError):
        OrderCreate(instrument_uid=uuid4(), direction="buy", quantity_lots=quantity)


@pytest.mark.parametrize("value", ["0", "-1", "0.0000000001", "NaN", "Infinity"])
def test_funding_precision_and_positive_amount(value):
    with pytest.raises(ValidationError):
        FundCreate(amount=value)


async def test_read_retries_server_error_then_succeeds(caplog):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"instruments": []}, headers={"x-tracking-id": "test-id"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        broker = TInvestClient(http, "secret-for-test")
        with caplog.at_level("INFO"):
            result = await broker.search("SBER")
    assert result.instruments == []
    assert len(calls) == 2
    assert all(str(c.url).startswith(BASE_URL) for c in calls)
    assert "secret-for-test" not in caplog.text
    assert "test-id" in caplog.text


async def test_auth_failure_does_not_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"message": "sensitive external message"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BrokerError) as caught:
            await TInvestClient(http, "fake").search("SBER")
    assert caught.value.code == "broker_auth"
    assert not caught.value.ambiguous
    assert len(calls) == 1
    assert "sensitive" not in str(caught.value)


async def test_trade_timeout_is_ambiguous_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("lost response", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BrokerError) as caught:
            await TInvestClient(http, "fake").post_order("account", uuid4(), 1, "buy", uuid4())
    assert caught.value.ambiguous
    assert len(calls) == 1


async def test_pay_in_never_retries():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BrokerError) as caught:
            await TInvestClient(http, "fake").fund("account", Money(units=100))
    assert caught.value.ambiguous
    assert len(calls) == 1


async def test_read_transport_failure_is_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BrokerError):
            await TInvestClient(http, "fake", read_attempts=2).search("SBER")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "error_type", [httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout]
)
async def test_connection_failure_is_not_ambiguous_and_never_retries_creation(error_type):
    calls = []

    def handler(request):
        calls.append(request)
        raise error_type("connection not established", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BrokerError) as caught:
            await TInvestClient(http, "fake").open_account("Test account")
    assert caught.value.code == "broker_connection_failed"
    assert not caught.value.ambiguous
    assert len(calls) == 1


@pytest.mark.parametrize("read", [True, False])
async def test_certificate_failure_is_explicit_and_not_retried(read):
    calls = []

    def handler(request):
        calls.append(request)
        certificate_error = ssl.SSLCertVerificationError(1, "certificate verify failed")
        raise httpx.ConnectError("TLS verification failed", request=request) from certificate_error

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        broker = TInvestClient(http, "fake", read_attempts=3)
        with pytest.raises(BrokerError) as caught:
            if read:
                await broker.search("SBER")
            else:
                await broker.open_account("Test account")
    assert caught.value.code == "broker_tls_verification_failed"
    assert not caught.value.ambiguous
    assert len(calls) == 1


@pytest.mark.parametrize("invalid_json", [True, False])
async def test_invalid_success_response_requires_reconciliation_without_retry(invalid_json):
    calls = []

    def handler(request):
        calls.append(request)
        if invalid_json:
            return httpx.Response(200, content=b"not-json")
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BrokerError) as caught:
            await TInvestClient(http, "fake").open_account("Test account")
    assert caught.value.code == "broker_invalid_response"
    assert caught.value.ambiguous
    assert len(calls) == 1
