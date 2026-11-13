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
npx eslint . && npx tsc --noEmit && npm run build
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

## What is tested today (Phase 1)

90 backend tests and 28 browser checks.

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

## Required scenarios and where they land

| Scenario | Phase | Status |
|---|---|---|
| Unauthorized access | 1 | Tested |
| Cross-tenant data access | 1 | Tested |
| Redis outage | 1, 2 | Tested for rate limiting; queue behaviour in Phase 2 |
| Duplicate webhook delivery | 2 | |
| Invalid webhook signatures and credentials | 2 | |
| Expired signals | 2 | |
| Database outage | 2 | Readiness tested now; ingest behaviour in Phase 2 |
| Application restart during execution | 2, 3 | |
| Partial fills | 3 | |
| Rejected orders | 3 | |
| Broker timeout | 3 | |
| Broker disconnection | 3 | |
| Ambiguous broker responses | 3 | |
| Duplicate worker execution | 3 | |
| Incorrect symbols | 4 | |
| Invalid lot sizes | 4 | |
| Invalid price precision | 4 | |
| Market closed | 4 | |
| Incorrect quantities | 5 | |
| Insufficient margin | 5 | |
| Multiple accounts receiving the same signal | 5 | |
| Kill switch activation | 5 | |
| Daily loss limit | 5 | |

A phase is not complete until its rows are tested.
