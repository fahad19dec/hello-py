"""Application factory for the API service."""

import logging

from fastapi import APIRouter, FastAPI

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.http import RequestContextMiddleware
from app.core.logging import configure_logging
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.bridge.router import router as bridge_router
from app.modules.hooks.router import router as hooks_router
from app.modules.system.router import router as system_router
from app.modules.trading.accounts import router as accounts_router
from app.modules.trading.activity import router as activity_router
from app.modules.trading.risk_api import router as risk_router
from app.modules.trading.strategies import router as strategies_router
from app.modules.trading.webhooks import router as webhooks_router
from app.modules.users.router import router as users_router

log = logging.getLogger("app")


def _init_sentry() -> None:
    settings = get_settings()
    if not settings.sentry_dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.version,
        send_default_pii=False,  # no cookies, headers, IPs or request bodies
        traces_sample_rate=0.0,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    _init_sentry()

    public_docs = settings.environment != "production"
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.version,
        docs_url="/api/docs" if public_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if public_docs else None,
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    api = APIRouter(prefix="/api/v1")
    api.include_router(system_router)
    api.include_router(auth_router)
    api.include_router(users_router)
    api.include_router(admin_router)
    api.include_router(accounts_router)
    api.include_router(strategies_router)
    api.include_router(webhooks_router)
    api.include_router(activity_router)
    api.include_router(risk_router)
    api.include_router(hooks_router)
    api.include_router(bridge_router)
    app.include_router(api)

    log.info(
        "started: environment=%s live_trading_enabled=%s paper_trading_enabled=%s",
        settings.environment,
        settings.live_trading_enabled,
        settings.paper_trading_enabled,
    )
    return app


app = create_app()
