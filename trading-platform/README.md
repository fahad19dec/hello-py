# Trading automation platform

Connects TradingView alerts to broker accounts (Zerodha Kite, MetaTrader 5,
MetaTrader 4) with risk controls, paper trading and a full audit trail.

"TradeBridge" is a placeholder name. It is set by `APP_NAME` (backend) and
`NEXT_PUBLIC_APP_NAME` (dashboard).

## Status

Broker accounts, webhook connections, risk management and order placement
work end to end. **Live trading is switched off** and stays off until a
compliance review is signed off (see [docs/compliance-notes.md](docs/compliance-notes.md)),
so today real orders cannot be sent; paper accounts trade in full.

| Works now | Not built yet |
|---|---|
| Accounts, email confirmation, two-factor, sessions, roles, audit trail | Analytics charts and reports |
| Broker accounts: paper, Zerodha Kite (daily login), MetaTrader 4/5 (terminal bridge) | Billing and plans |
| Webhooks: one-time URLs, passphrase, optional signature, rotation, revocation, dry-run tester | Support tickets |
| Signal intake with freshness checks and exactly-once storage | Zerodha instrument-master download (mappings are entered by hand) |
| Strategies with linked accounts, sizing modes and semi-automatic approval | Zerodha stop-loss orders (attached stops are refused instead) |
| Risk limits at global, account and strategy level; kill switches; platform halt; circuit breaker | Deployment, disaster-recovery and VPS guides |
| Execution engine with order states, retries only when nothing was sent, reconciliation of unknown outcomes | |
| Paper trading with slippage, fees, limit and stop orders, stop loss and take profit | |
| Orders, positions (with broker reconciliation for MetaTrader), signal log, trade history | |

Not yet tried against real brokers: the Zerodha adapter is tested against a
simulated broker and a mocked HTTP API, and the MetaTrader Expert Advisors
have not been compiled or run in a terminal. See the checklist in
[docs/metatrader-bridge.md](docs/metatrader-bridge.md).

## Run it with Docker

```bash
./scripts/dev-env.sh          # once: writes .env with fresh random secrets
docker compose up --build
```

| | |
|---|---|
| Dashboard | http://localhost:3000 |
| Emails sent by the app | http://localhost:8025 |
| API reference | http://localhost:8000/api/docs |

Create the first super admin (there are no default accounts):

```bash
docker compose exec api python -m app.cli create-admin --email you@example.com
```

Sign in, turn on two-factor authentication under Settings, and the admin area
appears.

## Run it without Docker

Needs Python 3.12+, Node 22, PostgreSQL 16 and Redis.

```bash
# database roles and database (as a PostgreSQL superuser)
psql -U postgres <<'SQL'
CREATE ROLE tp_owner LOGIN PASSWORD 'tp_owner_dev' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE tp_app   LOGIN PASSWORD 'tp_app_dev'   NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE tradeplatform OWNER tp_owner;
SQL

# API
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.lock
cp .env.example .env          # then set SECRET_KEY and DATA_ENCRYPTION_KEYS as the file explains
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --port 8000 --no-proxy-headers

# the execution worker, in another terminal
.venv/bin/python -m app.workers.run

# dashboard, in another terminal
cd frontend
npm ci
npm run dev
```

With `EMAIL_BACKEND=console` the confirmation and reset emails are printed in
the API's terminal.

## Tests

```bash
cd backend && .venv/bin/python -m pytest      # 167 tests, against a real PostgreSQL
cd frontend && npx eslint . && npm run typecheck && npm run build
```

Details, the browser test and the test plan for later phases are in
[docs/testing.md](docs/testing.md).

## Layout

```
backend/     FastAPI service, migrations, tests
frontend/    Next.js dashboard
bridge/      MetaTrader 4 and 5 Expert Advisors
e2e/         browser tests against the running stack
infra/       database bootstrap
scripts/     developer helpers
docs/        architecture, schema, security, testing, compliance notes
```

## Documentation

| Document | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, limits and alternatives, folder structure, broker integration, execution lifecycle, regulatory gate, roadmap |
| [docs/database-schema.md](docs/database-schema.md) | Tables built and planned, conventions, access contexts |
| [docs/security.md](docs/security.md) | Controls in force, the Phase 1 review, known gaps |
| [docs/testing.md](docs/testing.md) | How to run everything, what is covered, the plan for trading tests |
| [docs/compliance-notes.md](docs/compliance-notes.md) | Indian regulatory findings, sources and open questions |
| [docs/metatrader-bridge.md](docs/metatrader-bridge.md) | Installing the MetaTrader 4/5 bridge, and a demo-account checklist |
| Alert format | In the dashboard under API Documentation |

Guides still to come: VPS setup, admin, deployment, disaster recovery.

## Safety

- `LIVE_TRADING_ENABLED` defaults to `false`. Turning it on also requires
  `LIVE_TRADING_COMPLIANCE_APPROVAL`; without it the service refuses to start.
- Trading carries a risk of loss. This software executes instructions; it gives
  no advice and promises no returns.
