import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.api.routes import router
from app.core.config import Settings
from app.core.errors import AppError
from app.core.tls import tinvest_ssl_context
from app.db.session import database
from app.integrations.client import BrokerError, TInvestClient
from app.services.portfolio import PortfolioService
from app.workers.orders import order_worker


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        engine, sessions = database(config.database_url)
        try:
            timeout = httpx.Timeout(config.http_timeout, connect=config.http_connect_timeout)
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                verify=tinvest_ssl_context(config.tinvest_ca_file),
            ) as http:
                broker = TInvestClient(
                    http, config.tinvest_token.get_secret_value(), config.read_attempts
                )
                service = PortfolioService(sessions, broker)
                await service.recover_interrupted_fundings()
                application.state.settings = config
                application.state.sessions = sessions
                application.state.service = service
                task = asyncio.create_task(order_worker(service, config.order_poll_interval))
                application.state.worker = task
                try:
                    yield
                finally:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
        finally:
            await engine.dispose()

    application = FastAPI(
        title="Sandbox Portfolio",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Учебный backend. Все счета, рубли и сделки виртуальные. Только T-Invest sandbox."
        ),
    )
    application.include_router(router)

    @application.exception_handler(AppError)
    async def app_error(request: Request, error: AppError):
        return error_response(error.code, error.message, error.status)

    @application.exception_handler(BrokerError)
    async def broker_error(request: Request, error: BrokerError):
        message = (
            "Ответ внешнего API потерян; результат требует сверки, не повторяйте действие"
            if error.ambiguous
            else "Внешний API недоступен или отклонил запрос"
        )
        if error.code == "broker_connection_failed":
            message = "Не удалось установить соединение с песочницей T-Invest; запрос не отправлен"
        if error.code == "broker_tls_verification_failed":
            message = (
                "Не удалось проверить TLS-сертификат T-Invest; настройте доверенные сертификаты"
            )
        return error_response(error.code, message, 502)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return error_response("validation_error", "Проверьте формат и ограничения параметров", 422)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return error_response(
            "http_error", "Запрошенный маршрут или метод недоступен", error.status_code
        )

    @application.exception_handler(OSError)
    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError | OSError):
        logging.getLogger(__name__).error("database_operation_failed")
        return error_response(
            "database_unavailable", "Не удалось сохранить или прочитать данные", 503
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        logging.getLogger(__name__).error("unexpected_error type=%s", type(error).__name__)
        return error_response("internal_error", "Внутренняя ошибка приложения", 500)

    return application


app = create_app()
