import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.integrations.client import TInvestClient
from app.models.entities import (
    FundingRecord,
    Operation,
    Order,
    PortfolioSnapshot,
    SnapshotPosition,
)
from app.services.portfolio import PortfolioService
from app.workers.orders import order_worker, poll_once
from tests.fakes import UID, instrument


async def test_idempotency_and_conflict(scenario):
    service, account, fake = scenario
    first = await service.create_order(account.id, UID, "buy", 1, "same-key")
    second = await service.create_order(account.id, UID, "buy", 1, "same-key")
    assert first.id == second.id
    assert first.processing_state == "active"
    with pytest.raises(AppError) as caught:
        await service.create_order(account.id, UID, "buy", 2, "same-key")
    assert caught.value.status == 409
    assert sum(method == "PostSandboxOrder" for method, _ in fake.calls) == 1


async def test_concurrent_idempotency_is_enforced_by_database(scenario):
    service, account, fake = scenario
    another = PortfolioService(service.sessions, service.broker)
    first, second = await asyncio.gather(
        service.create_order(account.id, UID, "buy", 1, "race"),
        another.create_order(account.id, UID, "buy", 1, "race"),
    )
    assert first.id == second.id
    assert sum(method == "PostSandboxOrder" for method, _ in fake.calls) == 1
    async with service.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 1


async def test_timeout_survives_restart_and_never_reposts(scenario):
    service, account, fake = scenario
    fake.timeout_order = True
    order = await service.create_order(account.id, UID, "buy", 1, "timeout")
    assert order.processing_state == "unknown"
    duplicate = await service.create_order(account.id, UID, "buy", 1, "timeout")
    assert duplicate.id == order.id
    restarted = PortfolioService(service.sessions, service.broker)
    await poll_once(restarted)
    restored = await restarted.order(order.id)
    assert restored.processing_state == "finished"
    assert restored.lots_executed == 1
    assert sum(method == "PostSandboxOrder" for method, _ in fake.calls) == 1
    state_payload = next(
        payload for method, payload in fake.calls if method == "GetSandboxOrderState"
    )
    assert state_payload["orderId"] == str(order.broker_request_id)
    assert state_payload["orderIdType"] == "ORDER_ID_TYPE_REQUEST"


async def test_unrecoverable_order_remains_for_reconciliation(scenario):
    service, account, fake = scenario
    fake.timeout_order = True
    fake.state_error = True
    order = await service.create_order(account.id, UID, "buy", 1, "missing")
    await poll_once(service)
    restored = await service.order(order.id)
    assert restored.processing_state == "reconciliation_required"
    assert restored.broker_status is None
    assert sum(method == "PostSandboxOrder" for method, _ in fake.calls) == 1


async def test_sync_all_pages_and_no_duplicate_operations(scenario):
    service, account, fake = scenario
    first = await service.sync(account.id)
    second = await service.sync(account.id)
    assert first.id != second.id
    async with service.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Operation)) == 2
        assert await session.scalar(select(func.count()).select_from(PortfolioSnapshot)) == 2
        assert await session.scalar(select(func.count()).select_from(SnapshotPosition)) == 2
    rows = await service.list_records(Operation, account.id, 20, 0)
    assert {row.external_id for row in rows} == {"op1", "op2"}
    assert rows[0].payment == Decimal("-100.000000001")
    assert first.positions[0].value == Decimal("100.000000010")
    assert first.positions[0].quantity_units == 10
    assert len([p for method, p in fake.calls if method == "GetSandboxOperationsByCursor"]) == 4


async def test_snapshot_and_positions_rollback_together(scenario):
    service, account, fake = scenario
    fake.duplicate_positions = True
    with pytest.raises(IntegrityError):
        await service.sync(account.id)
    async with service.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PortfolioSnapshot)) == 0
        assert await session.scalar(select(func.count()).select_from(SnapshotPosition)) == 0
        assert await session.scalar(select(func.count()).select_from(Operation)) == 0


async def test_funding_timeout_is_recorded_without_retry(scenario):
    service, account, fake = scenario
    fake.fund_timeout = True
    result = await service.fund(account.id, Decimal("100000"))
    assert result.state == "unknown"
    assert sum(method == "SandboxPayIn" for method, _ in fake.calls) == 1
    rows = await service.list_records(FundingRecord, account.id, 20, 0)
    assert len(rows) == 1


async def test_sell_uses_units_and_unblocked_balance(scenario):
    service, account, fake = scenario
    fake.security_balance = 9
    with pytest.raises(AppError) as caught:
        await service.create_order(account.id, UID, "sell", 1, "sell-too-much")
    assert caught.value.code == "insufficient_position"
    fake.security_balance = 10
    fake.blocked_securities = 5
    order = await service.create_order(account.id, UID, "sell", 1, "sell-one")
    assert order.direction == "sell"
    payload = next(p for method, p in fake.calls if method == "PostSandboxOrder")
    assert payload["quantity"] == "1"
    assert payload["direction"] == "ORDER_DIRECTION_SELL"


async def test_worker_cancels_cleanly_and_continues_after_failure(scenario):
    service, account, fake = scenario
    fake.timeout_order = True
    fake.state_error = True
    order = await service.create_order(account.id, UID, "buy", 1, "worker")
    task = asyncio.create_task(order_worker(service, 0.01))
    try:
        for _ in range(100):
            if any(method == "GetSandboxOrderState" for method, _ in fake.calls):
                break
            await asyncio.sleep(0.01)
        fake.state_error = False
        for _ in range(100):
            if (await service.order(order.id)).processing_state == "finished":
                break
            await asyncio.sleep(0.01)
        assert (await service.order(order.id)).processing_state == "finished"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_search_finds_shares_and_funds_when_bonds_fill_unfiltered_results(sessions):
    share_uid = UID
    fund_uid = uuid4()
    bonds = [{"uid": str(uuid4()), "apiTradeAvailableFlag": True} for _ in range(25)]
    share = {"uid": str(share_uid), "apiTradeAvailableFlag": True}
    fund = {"uid": str(fund_uid), "apiTradeAvailableFlag": True}
    search_kinds = []
    detail_ids = []

    def handler(request):
        payload = json.loads(request.content)
        if request.url.path.endswith("/FindInstrument"):
            kind = payload.get("instrumentKind")
            search_kinds.append(kind)
            assert payload.get("apiTradeAvailableFlag") is True
            results = {
                "INSTRUMENT_TYPE_SHARE": [share, share],
                "INSTRUMENT_TYPE_ETF": [fund],
            }.get(kind, bonds + [share, fund])
            return httpx.Response(200, json={"instruments": results})
        if request.url.path.endswith("/GetInstrumentBy"):
            detail_ids.append(payload["id"])
            data = instrument()
            if payload["id"] == str(share_uid):
                data["instrument"]["ticker"] = "SBER"
            elif payload["id"] == str(fund_uid):
                data["instrument"].update(uid=str(fund_uid), ticker="TMOS", instrumentType="etf")
            else:
                data["instrument"].update(uid=payload["id"], instrumentType="bond")
            return httpx.Response(200, json=data)
        if request.url.path.endswith("/GetLastPrices"):
            return httpx.Response(200, json={"lastPrices": []})
        raise AssertionError("Unexpected broker method")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        service = PortfolioService(sessions, TInvestClient(http, "test-only"))
        result = await service.search("SBER")
    assert {item.ticker for item in result} == {"SBER", "TMOS"}
    assert set(search_kinds) == {"INSTRUMENT_TYPE_SHARE", "INSTRUMENT_TYPE_ETF"}
    assert len(detail_ids) == 2
