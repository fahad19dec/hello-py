"""The execution worker: python -m app.workers.run

Plans stored signals into orders, executes queued orders, and reconciles
anything whose outcome is not yet known. Several workers can run at once:
signals and orders are claimed with row locks, so each is handled once.

It wakes as soon as the webhook endpoint stores a signal (through Redis) and
otherwise polls, so a Redis outage only slows it down a little.
"""

import logging
import signal
import time

import redis

from app.core.config import get_settings
from app.core.db import new_session
from app.core.logging import configure_logging
from app.modules.hooks.router import WAKE_QUEUE
from app.trading import engine

log = logging.getLogger("app.worker")
_running = True


def _stop(*_):
    global _running
    _running = False


def _wait(client: redis.Redis | None, seconds: float) -> redis.Redis | None:
    if client is not None:
        try:
            client.blpop([WAKE_QUEUE], timeout=max(1, round(seconds)))
            return client
        except redis.RedisError:
            log.warning("Redis unavailable; polling instead")
    time.sleep(seconds)
    try:
        candidate = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.5, socket_timeout=5)
        candidate.ping()
        return candidate
    except redis.RedisError:
        return None


def main() -> None:
    configure_logging()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    settings = get_settings()
    log.info("worker %s started; live_trading_enabled=%s", engine.WORKER_ID, settings.live_trading_enabled)
    client: redis.Redis | None = None
    while _running:
        db = new_session()
        try:
            counts = engine.run_once(db)
            if counts["signals"] or counts["orders"]:
                log.info("processed %(signals)s signal(s), executed %(orders)s order(s)", counts)
        except Exception:  # noqa: BLE001 - keep the loop alive; leases make a failed pass safe to repeat
            log.exception("worker pass failed")
            db.rollback()
        finally:
            db.close()
        client = _wait(client, settings.worker_poll_seconds)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
