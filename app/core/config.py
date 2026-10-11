from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://portfolio:portfolio@localhost:5432/portfolio"
    tinvest_token: SecretStr = SecretStr("")
    tinvest_ca_file: Path | None = None
    http_timeout: float = Field(default=15, gt=0, le=300)
    http_connect_timeout: float = Field(default=5, gt=0, le=60)
    read_attempts: int = Field(default=3, ge=1, le=5)
    order_poll_interval: float = Field(default=15, ge=1, le=3600)

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL должен использовать postgresql+asyncpg")
        return value
