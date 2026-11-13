"""Health checks and the public system status (including the trading gates)."""

import logging

import redis
from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.db import get_engine

router = APIRouter(tags=["system"])
log = logging.getLogger("app.health")


@router.get("/health/live")
def live():
    """The process is up. Used by the orchestrator to decide whether to restart it."""
    return {"status": "ok"}


@router.get("/health/ready")
def ready(response: Response):
    """Whether this instance can serve traffic.

    The database is required. Redis is not: without it rate limits fall back to
    per-instance counters, so the instance reports "degraded" but stays in service.
    """
    components = {"database": "ok", "redis": "ok"}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        log.error("readiness: database unavailable (%s)", type(exc).__name__)
        components["database"] = "down"
    try:
        client = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.3, socket_timeout=0.3)
        client.ping()
        client.close()
    except redis.RedisError:
        components["redis"] = "down"

    if components["database"] != "ok":
        response.status_code = 503
        status = "unavailable"
    else:
        status = "ok" if components["redis"] == "ok" else "degraded"
    return {"status": status, "components": components}


@router.get("/system/status")
def system_status():
    """What the dashboard needs to know before sign-in. No secrets."""
    s = get_settings()
    return {
        "app_name": s.app_name,
        "version": s.version,
        "environment": s.environment,
        "live_trading_enabled": s.live_trading_enabled,
        "paper_trading_enabled": s.paper_trading_enabled,
    }
