import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.db.session import database
from app.integrations.client import TInvestClient
from app.services.portfolio import PortfolioService
from tests.fakes import FakeBroker


def validate_test_database_url(url: str) -> str:
    try:
        parsed = make_url(url)
    except ArgumentError as error:
        raise ValueError("TEST_DATABASE_URL должен быть корректным URL PostgreSQL") from error
    if parsed.drivername != "postgresql+asyncpg":
        raise ValueError("TEST_DATABASE_URL должен использовать postgresql+asyncpg")
    if not parsed.database or not parsed.database.endswith("_test"):
        raise ValueError("Тесты допускают только отдельную базу с суффиксом _test")
    if "database" in parsed.query:
        raise ValueError("Имя тестовой базы нельзя переопределять через параметры URL")
    return url


@pytest.fixture(scope="session")
def migrated_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("Задайте TEST_DATABASE_URL для отдельной PostgreSQL с именем *_test")
    try:
        validate_test_database_url(url)
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)
    environment = os.environ.copy()
    environment["DATABASE_URL"] = url
    environment["TINVEST_TOKEN"] = ""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "Не удалось применить миграции: проверьте подключение к тестовой базе", pytrace=False
        )
    return url


@pytest_asyncio.fixture
async def sessions(migrated_url):
    engine, factory = database(migrated_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE accounts, instruments, orders, operations, portfolio_snapshots, "
                "portfolio_snapshot_positions, fundings CASCADE"
            )
        )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def scenario(sessions):
    fake = FakeBroker()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as http:
        service = PortfolioService(sessions, TInvestClient(http, "test-only", read_attempts=1))
        account = await service.create_account("Учебный портфель")
        yield service, account, fake
