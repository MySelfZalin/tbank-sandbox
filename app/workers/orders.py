import asyncio
import logging

from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError
from app.integrations.client import BrokerError
from app.services.portfolio import PortfolioService

logger = logging.getLogger(__name__)


async def poll_once(service: PortfolioService) -> None:
    for order_id in await service.pending_order_ids():
        try:
            await service.refresh_order(order_id)
        except (BrokerError, AppError, SQLAlchemyError, OSError):
            logger.warning("order_poll_failed order_id=%s", order_id)


async def order_worker(service: PortfolioService, interval: float) -> None:
    while True:
        try:
            await poll_once(service)
        except (SQLAlchemyError, OSError):
            logger.warning("order_poll_database_unavailable")
        await asyncio.sleep(interval)
