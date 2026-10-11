import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx

UID = uuid4()


def money(units: int = 0, nano: int = 0) -> dict[str, str | int]:
    return {"units": str(units), "nano": nano, "currency": "rub"}


def instrument() -> dict[str, dict[str, object]]:
    return {
        "instrument": {
            "uid": str(UID),
            "ticker": "TEST",
            "name": "Учебная акция",
            "lot": 10,
            "currency": "rub",
            "instrumentType": "share",
            "apiTradeAvailableFlag": True,
            "buyAvailableFlag": True,
            "sellAvailableFlag": True,
        }
    }


class FakeBroker:
    def __init__(self) -> None:
        self.calls = []
        self.timeout_order = False
        self.state_error = False
        self.fund_timeout = False
        self.duplicate_positions = False
        self.security_balance = 10
        self.blocked_securities = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        self.calls.append((method, payload))
        if method == "FindInstrument":
            return httpx.Response(
                200, json={"instruments": [{"uid": str(UID), "apiTradeAvailableFlag": True}]}
            )
        if method == "GetInstrumentBy":
            return httpx.Response(200, json=instrument())
        if method == "GetLastPrices":
            return httpx.Response(
                200,
                json={
                    "lastPrices": [
                        {
                            "instrumentUid": uid,
                            "price": {"units": "286", "nano": 200000001},
                            "time": "2026-10-09T15:45:00Z",
                        }
                        for uid in payload["instrumentId"]
                    ]
                },
            )
        if method == "OpenSandboxAccount":
            return httpx.Response(200, json={"accountId": "sandbox-account"})
        if method == "SandboxPayIn":
            if self.fund_timeout:
                raise httpx.ReadTimeout("lost", request=request)
            return httpx.Response(200, json={"balance": money(100000)})
        if method == "PostSandboxOrder":
            if self.timeout_order:
                raise httpx.ReadTimeout("accepted but lost", request=request)
            return httpx.Response(
                200,
                json={
                    "orderId": "broker-order",
                    "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
                },
            )
        if method == "GetSandboxOrderState":
            if self.state_error:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "orderId": "broker-order",
                    "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                    "lotsExecuted": "1",
                },
            )
        if method == "GetSandboxOperationsByCursor":
            second = payload.get("cursor") == "page2"
            return httpx.Response(
                200,
                json={
                    "hasNext": not second,
                    "nextCursor": "" if second else "page2",
                    "items": [
                        {
                            "id": "op2" if second else "op1",
                            "date": datetime.now(UTC).isoformat(),
                            "type": "OPERATION_TYPE_BUY",
                            "state": "OPERATION_STATE_EXECUTED",
                            "instrumentUid": str(UID),
                            "quantity": "10",
                            "quantityDone": "10",
                            "payment": money(-100, -1),
                            "price": money(10),
                        }
                    ],
                },
            )
        if method == "GetSandboxPortfolio":
            position = {
                "instrumentUid": str(UID),
                "quantity": {"units": "10", "nano": 0},
                "currentPrice": money(10, 1),
                "averagePositionPrice": money(10),
            }
            return httpx.Response(
                200,
                json={
                    "totalAmountPortfolio": money(100000, 1),
                    "totalAmountShares": money(100, 10),
                    "totalAmountCurrencies": money(99900),
                    "positions": [position, position] if self.duplicate_positions else [position],
                },
            )
        if method == "GetSandboxPositions":
            return httpx.Response(
                200,
                json={
                    "money": [money(99900)],
                    "blocked": [money(100)],
                    "securities": [
                        {
                            "instrumentUid": str(UID),
                            "balance": str(self.security_balance),
                            "blocked": str(self.blocked_securities),
                        }
                    ],
                },
            )
        raise AssertionError(f"Unexpected method: {method}")
