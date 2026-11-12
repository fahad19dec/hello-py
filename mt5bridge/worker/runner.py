"""Runs one MT5 account: logs its terminal in, then executes orders from the API.

One process per account, because the MetaTrader5 package binds a Python
process to a single terminal. Started by worker.supervisor.
"""
import argparse
import logging
import os
import sys
import time

from .executor import MT5Error, MT5Executor

log = logging.getLogger("runner")

POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15


class AccountGone(Exception):
    """The account was removed or paused; the runner should stop."""


class Api:
    """Thin client for the bridge's worker endpoints.

    `session` needs requests-style get/post (requests.Session, or a test client).
    """

    def __init__(self, session, base_url: str, worker_key: str):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Worker-Key": worker_key}

    def _call(self, method: str, path: str, json=None):
        url = self.base_url + path
        if method == "post":
            response = self.session.post(url, headers=self.headers, json=json, timeout=10)
        else:
            response = self.session.get(url, headers=self.headers, timeout=10)
        if response.status_code == 404:
            raise AccountGone(path)
        if response.status_code == 409:
            return {}  # result already recorded (e.g. the order timed out)
        response.raise_for_status()
        return response.json()

    def account(self, account_id: int) -> dict:
        return self._call("get", f"/worker/accounts/{account_id}")

    def status(self, account_id: int, report: dict) -> None:
        self._call("post", f"/worker/accounts/{account_id}/status", report)

    def claim(self, account_id: int) -> list[dict]:
        return self._call("post", f"/worker/accounts/{account_id}/claim")["orders"]

    def result(self, order_id: int, result: dict) -> None:
        self._call("post", f"/worker/orders/{order_id}/result", result)


def report_result(api: Api, order_id: int, result: dict, attempts: int = 5, sleep=time.sleep) -> None:
    """The trade is already done, so try hard to record it."""
    for attempt in range(attempts):
        try:
            api.result(order_id, result)
            return
        except AccountGone:
            return
        except Exception as exc:  # network trouble: retry
            log.warning("could not report order %s (%s), retrying", order_id, exc)
            sleep(2 * (attempt + 1))
    log.error("giving up reporting order %s: %s", order_id, result)


def process_once(api: Api, executor: MT5Executor, account_id: int) -> int:
    """Claim and execute whatever is queued. Returns the number of orders handled."""
    orders = api.claim(account_id)
    for order in orders:
        result = executor.execute(order)
        log.info("order %s -> %s", order["id"], result)
        report_result(api, order["id"], result)
    return len(orders)


def run(api: Api, executor: MT5Executor, account_id: int, terminal_path: str) -> int:
    try:
        account = api.account(account_id)
    except AccountGone:
        return 0
    try:
        executor.connect(terminal_path, account["login"], account["password"], account["server"])
    except MT5Error as exc:
        log.error("%s", exc)
        api.status(account_id, {"status": "error", "message": str(exc)})
        return 1

    last_heartbeat = 0.0
    try:
        while True:
            try:
                if time.monotonic() - last_heartbeat > HEARTBEAT_SECONDS:
                    api.status(account_id, executor.health())
                    last_heartbeat = time.monotonic()
                process_once(api, executor, account_id)
            except AccountGone:
                log.info("account %s removed or paused, stopping", account_id)
                return 0
            except Exception as exc:  # API unreachable: keep the terminal up and retry
                log.warning("bridge API problem: %s", exc)
                time.sleep(5)
            time.sleep(POLL_SECONDS)
    finally:
        executor.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--terminal-path", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s acct={args.account_id} %(message)s")

    import MetaTrader5 as mt5  # Windows only
    import requests

    api = Api(requests.Session(), os.environ["MT5BRIDGE_API_URL"], os.environ["MT5BRIDGE_WORKER_KEY"])
    return run(api, MT5Executor(mt5), args.account_id, args.terminal_path)


if __name__ == "__main__":
    sys.exit(main())
