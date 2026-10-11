import pytest

from tests.conftest import validate_test_database_url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://user:password@localhost/portfolio_test",
        "postgresql+asyncpg://user:password@localhost/portfolio_test?application_name=pytest",
    ],
)
def test_database_url_accepts_dedicated_postgres_database(url):
    assert validate_test_database_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://user:password@localhost/portfolio",
        "postgresql+asyncpg://user:password@localhost/portfolio?password=example_test",
        "postgresql+asyncpg://user:password@localhost/portfolio_test?database=portfolio",
        "postgresql+asyncpg://user:password@localhost/portfolio_test?database=another_test",
        "postgresql+asyncpg://user:password@localhost/",
        "sqlite:///portfolio_test",
        "not-a-database-url",
    ],
)
def test_database_url_rejects_working_database_and_query_override(url):
    with pytest.raises(ValueError):
        validate_test_database_url(url)
