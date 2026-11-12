"""Keeps one runner process (and one MT5 terminal copy) alive per enabled account.

Run on the Windows server:  python -m worker.supervisor

Environment:
  MT5BRIDGE_API_URL            https://your-bridge.example.com
  MT5BRIDGE_WORKER_KEY         same value as on the web server
  MT5BRIDGE_TERMINAL_TEMPLATE  folder holding a clean MT5 install (terminal64.exe)
  MT5BRIDGE_TERMINALS_DIR      folder where per-account copies are created
"""
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

log = logging.getLogger("supervisor")

SYNC_SECONDS = 20
RESTART_DELAY_SECONDS = 30


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s supervisor %(message)s")
    api_url = os.environ["MT5BRIDGE_API_URL"].rstrip("/")
    headers = {"X-Worker-Key": os.environ["MT5BRIDGE_WORKER_KEY"]}
    template = Path(os.environ["MT5BRIDGE_TERMINAL_TEMPLATE"])
    terminals = Path(os.environ["MT5BRIDGE_TERMINALS_DIR"])
    terminals.mkdir(parents=True, exist_ok=True)

    runners: dict[int, subprocess.Popen] = {}
    started_at: dict[int, float] = {}

    while True:
        try:
            response = requests.get(f"{api_url}/worker/accounts", headers=headers, timeout=10)
            response.raise_for_status()
            wanted = set(response.json()["account_ids"])
        except Exception as exc:
            log.warning("could not reach the bridge API: %s", exc)
            time.sleep(SYNC_SECONDS)
            continue

        for account_id in list(runners):
            process = runners[account_id]
            if account_id not in wanted:
                log.info("stopping account %s", account_id)
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                del runners[account_id]
                # The terminal folder caches the login, so remove it with the account.
                shutil.rmtree(terminals / str(account_id), ignore_errors=True)
            elif process.poll() is not None:
                log.warning("runner for account %s exited with %s", account_id, process.returncode)
                del runners[account_id]

        for account_id in sorted(wanted - set(runners)):
            if time.monotonic() - started_at.get(account_id, -RESTART_DELAY_SECONDS) < RESTART_DELAY_SECONDS:
                continue
            folder = terminals / str(account_id)
            if not folder.exists():
                shutil.copytree(template, folder)
            log.info("starting account %s", account_id)
            runners[account_id] = subprocess.Popen(
                [
                    sys.executable, "-m", "worker.runner",
                    "--account-id", str(account_id),
                    "--terminal-path", str(folder / "terminal64.exe"),
                ]
            )
            started_at[account_id] = time.monotonic()

        time.sleep(SYNC_SECONDS)


if __name__ == "__main__":
    main()
