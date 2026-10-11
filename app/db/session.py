from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def database(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def database_is_ready(sessions: async_sessionmaker[AsyncSession]) -> bool:
    async with sessions() as session:
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
    return version == "0001"
