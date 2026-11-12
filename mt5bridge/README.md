# Trade Bridge (prototype)

> **Status: prototype.** This folder is the first proof of concept. The
> production platform is being built in `../trading-platform/`, which reuses the
> broker logic and simulators from here. Nothing in this folder has been run
> against a real broker.


A hosted TradingView-to-MetaTrader 5 bridge. A customer signs up, enters their
MT5 login, and gets a private webhook URL. TradingView alerts sent to that URL
become market orders on their MT5 account. The customer installs nothing.

## How it fits together

```
TradingView alert ──► web server (any Linux host)        Windows server(s)
                      app/  FastAPI + database     ◄──►  worker/  one MT5 terminal
                      - webhook, order queue             per customer account
                      - dashboard, accounts
```

- `app/` is the website: sign-up, dashboard, the `/hook/<token>` webhook and the
  order queue. It runs anywhere.
- `worker/` runs on Windows, because the official `MetaTrader5` Python package
  only works next to a Windows terminal. `supervisor.py` copies a clean MT5
  install per account and starts one `runner.py` per account, which logs the
  terminal in, polls the web server for orders and reports fills back.

## Alert format

```json
{"action": "buy", "symbol": "{{ticker}}", "lots": 0.1, "sl": 2650.0, "tp": 2700.0}
```

| Field | Meaning |
|---|---|
| `action` | `buy`, `sell`, or `close` (closes every position on the symbol) |
| `symbol` | Symbol name; an exchange prefix such as `OANDA:` is stripped |
| `lots` | Volume; required for buy and sell |
| `sl`, `tp` | Optional stop-loss and take-profit prices |
| `reverse` | `true` closes opposite positions on the symbol before opening |
| `account` | Name of one account to trade; default is all enabled accounts |
| `comment` | Order comment, up to 31 characters |

## Zerodha (Kite Connect)

Zerodha accounts need no Windows server: the web server calls the Kite Connect
API directly. The customer creates a Kite Connect app, pastes its key and secret
into the dashboard, and logs in to Zerodha once every trading day (tokens expire
at 6:00 IST). An alert with `quantity` trades Zerodha accounts; one with `lots`
trades MT5 accounts.

```json
{"action": "buy", "symbol": "RELIANCE", "exchange": "NSE", "quantity": 10, "product": "MIS"}
```

Rules checked against Zerodha's documentation in November 2026:

- Market orders must carry `market_protection`; the bridge sends `-1` (automatic).
- API orders are rejected unless they come from a static IP registered in the
  customer's Kite developer profile. **Each IP can be linked to one developer
  account and shared only with immediate family**, so one server cannot place
  orders for unrelated customers. This prototype therefore only suits your own
  or family accounts.
- An accepted order is shown as `placed`, not filled: Zerodha's reply only
  confirms the order was accepted.

## Safety rules built in

- MT5 passwords are encrypted at rest and never shown again in the dashboard.
- Each account has a maximum lot size; larger orders are rejected.
- An order not picked up within 60 seconds is expired, never traded late.
- Each order is handed to a worker once. If the worker never reports back, the
  order is marked failed and is not retried, to avoid double trades.
- Webhook calls are rate-limited per user and every refused alert is shown on
  the dashboard with the reason.

## Run the web server

```bash
pip install -r requirements.txt
export MT5BRIDGE_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export MT5BRIDGE_WORKER_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export MT5BRIDGE_FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export MT5BRIDGE_PUBLIC_URL=https://your-domain.example
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Keep the three keys safe and unchanged: losing `MT5BRIDGE_FERNET_KEY` makes the
stored MT5 passwords unreadable. In production also set `MT5BRIDGE_HTTPS_ONLY=1`,
put the server behind HTTPS on port 443 (TradingView only calls ports 80 and
443), and point `MT5BRIDGE_DATABASE_URL` at Postgres. The default is a local
SQLite file.

## Run the worker (Windows server)

1. Install MetaTrader 5 once, enable **Algo Trading**, and copy its install
   folder (the one containing `terminal64.exe`) to use as the template.
2. Install Python, then `pip install -r worker/requirements.txt`.
3. Set `MT5BRIDGE_API_URL`, `MT5BRIDGE_WORKER_KEY`,
   `MT5BRIDGE_TERMINAL_TEMPLATE` and `MT5BRIDGE_TERMINALS_DIR`.
4. From this folder run `python -m worker.supervisor`.

## Tests

```bash
python -m pytest
```

The tests use a fake MT5 module, so they run on any OS. The worker has not been
run against a real terminal yet; do that on a demo account first.

## Not built yet

Billing and plans (Razorpay), email verification and password reset, Telegram
notifications, paper trading, pending/limit orders, and spreading accounts
across several Windows servers.
