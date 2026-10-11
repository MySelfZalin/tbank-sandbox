import asyncio
from decimal import Decimal

import httpx

from app.core.config import Settings
from app.main import create_app
from app.models.entities import FundingRecord
from tests.fakes import UID


async def test_http_workflow_and_pagination(scenario):
    service, account, fake = scenario
    app = create_app(Settings(_env_file=None, tinvest_token="test-only"))
    app.state.service = service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as http:
        prefix = f"/accounts/{account.id}"
        funded = await http.post(prefix + "/fund", json={"amount": "100000"})
        assert funded.status_code == 200
        assert funded.json()["state"] == "confirmed"
        found = await http.get("/instruments/search", params={"query": "TEST"})
        assert found.json()[0]["lot"] == 10
        assert found.json()[0]["last_price"] == "286.200000001"
        assert found.json()[0]["last_price_at"] == "2026-10-09T15:45:00Z"
        assert found.json()[0]["quote_status"] == "available"
        payload = {"instrument_uid": str(UID), "direction": "buy", "quantity_lots": 1}
        created = await http.post(
            prefix + "/orders", json=payload, headers={"Idempotency-Key": "api-order"}
        )
        assert created.status_code == 200
        assert created.json()["processing_state"] == "active"
        duplicate = await http.post(
            prefix + "/orders", json=payload, headers={"Idempotency-Key": "api-order"}
        )
        assert duplicate.json()["id"] == created.json()["id"]
        synced = await http.post(prefix + "/sync")
        assert synced.status_code == 200
        assert synced.json()["total_value"] == "100000.000000001"
        assert synced.json()["positions"][0]["quantity_units"] == "10"
        state = await http.get("/orders/" + created.json()["id"])
        assert state.json()["broker_status"] == "EXECUTION_REPORT_STATUS_FILL"
        current = await http.get(prefix + "/portfolio")
        assert current.json()["id"] == synced.json()["id"]
        page1 = await http.get(prefix + "/operations", params={"limit": 1})
        page2 = await http.get(prefix + "/operations", params={"limit": 1, "offset": 1})
        assert page1.json()[0]["external_id"] != page2.json()[0]["external_id"]
        assert (await http.get(prefix + "/portfolio/history")).json()[0]["id"] == synced.json()[
            "id"
        ]
        assert (await http.get(prefix + "/fundings")).json()[0]["state"] == "confirmed"


async def test_api_errors_are_uniform_and_sanitized(scenario):
    service, account, fake = scenario
    app = create_app(Settings(_env_file=None, tinvest_token="test-only"))
    app.state.service = service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as http:
        invalid = await http.post(
            f"/accounts/{account.id}/orders",
            json={"instrument_uid": str(UID), "direction": "buy", "quantity_lots": 0},
            headers={"Idempotency-Key": "invalid"},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "validation_error"
        bad_page = await http.get(f"/accounts/{account.id}/orders", params={"limit": 101})
        assert bad_page.status_code == 422
        missing = await http.get("/orders/00000000-0000-0000-0000-000000000000")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "order_not_found"
        period = await http.get(
            f"/accounts/{account.id}/portfolio/history",
            params={"start": "2026-10-09T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
        )
        assert period.status_code == 400
        assert "test-only" not in invalid.text + missing.text + period.text


async def test_lifespan_resumes_saved_order_and_stops_worker(scenario, migrated_url, monkeypatch):
    service, account, fake = scenario
    fake.timeout_order = True
    order = await service.create_order(account.id, UID, "buy", 1, "lifespan-restart")
    interrupted_funding = FundingRecord(account_id=account.id, amount=Decimal("100"))
    async with service.sessions() as session, session.begin():
        session.add(interrupted_funding)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        return original_client(transport=httpx.MockTransport(fake.handle), **kwargs)

    monkeypatch.setattr("app.main.httpx.AsyncClient", mocked_client)
    app = create_app(
        Settings(
            _env_file=None,
            database_url=migrated_url,
            tinvest_token="test-only",
            order_poll_interval=1,
        )
    )
    async with app.router.lifespan_context(app):
        async with original_client(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as http:
            assert (await http.get("/health/ready")).status_code == 200
            for _ in range(100):
                response = await http.get(f"/orders/{order.id}")
                if response.json()["processing_state"] == "finished":
                    break
                await asyncio.sleep(0.01)
            assert response.json()["processing_state"] == "finished"
    async with service.sessions() as session:
        funding = await session.get(FundingRecord, interrupted_funding.id)
        assert funding.state == "unknown"
        assert funding.error_code == "processing_interrupted"
    assert app.state.worker.cancelled()
    assert sum(method == "PostSandboxOrder" for method, _ in fake.calls) == 1
