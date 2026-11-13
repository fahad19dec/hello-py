# Testing

No automated test places a real order, and none will. Broker behaviour is
simulated.

## Running the tests

### Backend

Needs PostgreSQL 16 with the two roles the application uses, and optionally
Redis (one test is skipped without it).

```bash
# once: roles and an empty test database
psql -U postgres <<'SQL'
CREATE ROLE tp_owner LOGIN PASSWORD 'tp_owner_dev' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE tp_app   LOGIN PASSWORD 'tp_app_dev'   NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE tradeplatform_test OWNER tp_owner;
SQL

cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.lock
.venv/bin/python -m pytest
```

Other connection details go in `TEST_DATABASE_URL`,
`TEST_MIGRATIONS_DATABASE_URL` and `TEST_REDIS_URL`. The suite builds the schema
by running the real migrations, and the application connects as the restricted
role exactly as in production. That is what makes the isolation tests mean
something.

### Dashboard

```bash
cd frontend
npm ci
npx eslint . && npm run typecheck && npm run build
```

### Browser test

`e2e/phase1.e2e.js` drives a real browser through registration, email
confirmation, sign-in, two-factor setup, admin suspension, password reset and
the phone layout, against the running API and the production build of the
dashboard. It needs Playwright with Chromium, the API started with
`EMAIL_BACKEND=console` and its output written to a file, and the dashboard on
port 3000.

```bash
node e2e/phase1.e2e.js ./screenshots ./backend.log ./backend
```

### Container stack

`docker compose up --build` after `./scripts/dev-env.sh`. Verified on
13 November 2026: all services healthy, migrations applied, a sign-up went
through with the email delivered by SMTP to the mail catcher, the API ran as an
unprivileged user on a read-only filesystem, and the application's database
role saw no rows without a tenant context.

### Continuous integration

`.github/workflows/trading-platform.yml` runs the backend tests against
PostgreSQL and Redis containers, lints, builds the dashboard, and audits both
dependency sets on every push that touches `trading-platform/`.

## What is tested today

167 backend tests and 49 browser checks (28 for accounts and security, 21 for trading).

| Area | Examples |
|---|---|
| Configuration | Live trading off by default; cannot be enabled without a recorded compliance approval; unsafe production settings refused |
| Encryption | Round trip; a value moved to another row fails; key rotation; tampering detected |
| Registration | Confirm then sign in; existing address reveals nothing; re-registration; single-use and expiring links; weak passwords refused with a reason |
| Sign-in and sessions | Identical answer for wrong password and unknown address; lockout applies to both; cookie flags; logout ends the session server-side; idle and absolute expiry; per-device revocation; CSRF token required; cross-site writes refused |
| Two-factor | Not active until a code is confirmed; pending session can do nothing else; codes cannot be replayed; recovery codes single-use; five wrong codes end the session |
| Passwords | Change signs out other devices; reset is single-use, signs out everything, keeps two-factor |
| Admin | Regular users get 404; staff need two-factor; support cannot change roles or touch staff; nobody can change their own role; every action audited |
| Tenant isolation | API and raw-SQL tests for reads, updates, deletes and inserts across tenants; staff cannot read credentials; audit log immutable even for the table owner; context does not leak across pooled connections |
| Structure | Every table has forced row-level security; every route requires a session unless listed public; models match migrations |
| Secrets | No password, token, authenticator secret or recovery code appears in logs or audit rows after a full run |
| Resilience | Sign-in still works and is still throttled with Redis down; readiness reflects database and Redis state |

## Strategy for the trading phases

**A simulated broker** implements the same adapter interface as real brokers
and is scripted per test: accept, reject with a reason, fill partially then
fully, time out, drop the reply after accepting, disconnect, return an
unexpected body. Every real adapter must pass the same lifecycle suite against
recorded or mocked broker responses.

**Fault injection** at chosen points: kill the worker between claiming an order
and submitting it, and between submitting and recording; make Redis or the
database unavailable; deliver the same message twice; run several workers
against one order.

**Layers.** Unit tests for pure logic (sizing, validation, risk rules). Service
tests against real PostgreSQL for anything involving isolation, idempotency or
locking. Adapter tests against mocked HTTP. Browser tests for the critical user
paths. The MT4 and MT5 Expert Advisors are exercised by hand against a demo
terminal, with a written checklist, because they cannot run here.

## Required scenarios

| Scenario | Where it is tested |
|---|---|
| Duplicate webhook delivery | `test_webhook_intake.py`: same id twice and three times; identical body without an id |
| Invalid webhook signatures and credentials | wrong or missing passphrase; missing, wrong and tampered HMAC; unknown, rotated and revoked URLs |
| Expired signals | too old, from the future, per-strategy and per-account age limits; queued orders past their deadline expire |
| Incorrect quantities | not a multiple of the lot or step; below minimum; above limits; sizing that needs a missing price or stop |
| Incorrect symbols | Zerodha alert with no instrument mapping; terminal reports the symbol does not exist |
| Invalid lot sizes | lot-size test (NIFTY lot 75); MetaTrader EA checks volume min, max and step before sending |
| Invalid price precision | tick-size test; default stops placed on a valid tick |
| Insufficient margin | minimum free margin rule; broker refusal "Insufficient funds" recorded as broker_rejected |
| Market closed | Zerodha market hours (weekday, weekend, MCX evening); terminal "Market is closed" |
| Broker timeout | reply lost after the broker accepted: UNKNOWN, found by tag, filled, never re-sent |
| Broker disconnection | refused connection retried because nothing was sent; broker unreachable during reconciliation leaves the order UNKNOWN |
| Partial fills | Zerodha order filled 4 then 10; positions follow |
| Rejected orders | broker refusal; three in a row open the circuit breaker; reset |
| Duplicate worker execution | two workers, one claim; a worker that does not own an order cannot execute it |
| Database outage | webhook answers 500 and nothing is half-recorded; readiness reports unavailable |
| Redis outage | alerts still accepted and processed (the worker polls); sign-in still throttled |
| Application restart during execution | paper order re-queued once; Zerodha and MetaTrader orders reconciled before anything else |
| Ambiguous broker responses | UNKNOWN until checked; "not found" only after a grace period; terminal lookup |
| Multiple accounts receiving the same signal | three accounts, one refuses by its own limit, the others fill |
| Kill switch activation | account, strategy, user-wide (including already-queued orders) and platform halt |
| Daily loss limit | locks entries, never blocks closing |
| Unauthorized access | every route refuses anonymous requests unless listed public |
| Cross-tenant data access | API and raw SQL for every trading table; staff cannot read broker credentials |

The browser test `e2e/trading.e2e.js` (21 checks) drives the same flows through the
dashboard with the real worker: adding accounts, pairing a terminal, linking a
strategy, creating and testing a webhook, sending alerts, a duplicate, a risk
rejection, the kill switch, and approving a semi-automatic order.
