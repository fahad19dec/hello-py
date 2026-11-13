# Architecture

This document covers the system design, the folder structure, the broker
integration design, the execution lifecycle, the regulatory gate and the build
roadmap. The database is in [database-schema.md](database-schema.md), security
in [security.md](security.md) and testing in [testing.md](testing.md).

Status markers used throughout: **Built** means implemented and tested in this
repository. **Planned** means designed here but not written yet.

## 1. Starting point

What was in the repository before this platform:

| Item | What it is | Decision |
|---|---|---|
| `Hello.py`, `pages/`, `utils.py`, `requirements.txt` | The stock Streamlit "hello" starter. Unrelated to this product. | Left untouched. |
| `mt5bridge/` | A prototype written earlier: FastAPI, SQLite, server-rendered pages, a Windows worker that hosts MT5 terminals, and a Zerodha client. 38 tests against fake brokers. | Kept as reference; superseded by this platform. |

Weaknesses of the prototype against the requirements, which shaped this design:

- Tenant isolation relied on each query remembering to filter by user.
- No email verification, two-factor, roles, sessions list or audit trail.
- Webhooks had no idempotency, replay protection or signal-age check.
- Zerodha orders were sent from the web request; nothing was durable.
- No order state machine: "placed" and "filled" were not distinguished for MT5.
- It stored MT5 account passwords, which the requirements rule out by default.
- SQLite, one process, no migrations.

What carries over, to be ported in Phase 4: the MT5 execution logic (retcodes,
filling modes, volume normalisation), the Zerodha client (login flow, market
protection, ambiguous-reply handling) and the two broker simulators.

## 2. System architecture

```
                        ┌──────────────────────────────────────────────┐
  Browser ──HTTPS──►    │  Edge: TLS, WAF, rate limits                 │
  TradingView ──────►   └───────┬──────────────────┬───────────────────┘
  MT4/MT5 bridge EA ─►          │                  │
                         ┌──────▼──────┐   ┌───────▼────────┐
                         │  Dashboard  │   │  API (FastAPI) │  auth, strategies, accounts,
                         │  (Next.js)  │──►│                │  risk settings, admin, billing,
                         └─────────────┘   │  + WebSocket   │  bridge gateway for EAs
                                           └───┬────────┬───┘
                          ┌────────────────────┘        │
                 ┌────────▼────────┐            ┌───────▼───────┐
                 │ Ingest (FastAPI)│            │  PostgreSQL   │  source of truth,
                 │ POST /hooks/... │───────────►│  (RLS)        │  durable work queue
                 └────────┬────────┘            └───────▲───────┘
                          │ wake-up                     │
                 ┌────────▼────────┐            ┌───────┴───────────────────────────┐
                 │ Redis           │◄──────────►│ Workers (Celery)                  │
                 │ broker, limits, │            │  signal processor · risk engine   │
                 │ pub/sub         │            │  executors (kite, bridge, paper)  │
                 └─────────────────┘            │  reconciler · scheduler · notifier│
                                                └───────┬───────────────────────────┘
                                                        │ static egress
                                             Zerodha Kite Connect API
```

| Component | Role | Status |
|---|---|---|
| Dashboard | Next.js UI. Talks to the API on the same origin, so the session cookie is first-party and no CORS is needed. | **Built** (Phase 1 screens) |
| API | Accounts, sessions, two-factor, admin, and later strategies, broker accounts, risk settings, billing. Also the gateway the MT4/MT5 bridge polls. | **Built** for identity and admin |
| Ingest | The webhook endpoint, deployable on its own. It authenticates, validates, de-duplicates, stores the signal and answers. It never calls a broker. | Planned, Phase 2 |
| Workers | Turn signals into per-account orders, apply risk rules, submit to brokers, reconcile. | Planned, Phases 2 to 5 |
| PostgreSQL | The only source of truth. Row-level security isolates tenants. Also holds the durable work queue. | **Built** |
| Redis | Rate limits now; later the Celery transport and live-update fan-out. Never the only copy of anything. | **Built** for rate limits |

### Design rules

1. **Capital protection first.** Any doubt resolves to not sending an order.
   Live trading is off by default and behind a compliance gate (section 7).
2. **PostgreSQL is the queue of record.** A signal is committed before the
   webhook answers. Redis only wakes workers; a sweeper re-dispatches anything
   committed but not picked up. Losing Redis loses no signal and duplicates no order.
3. **One winner per order.** Workers claim an order with a conditional update,
   so two workers can never both submit it.
4. **Ambiguity is a state, not an error.** A lost reply moves the order to
   `UNKNOWN`. It is reconciled against the broker before anything is retried.
5. **Evidence before "filled".** An order is `FILLED` only when the broker
   reports the fill. A returned order id means `SUBMITTED`.
6. **Brokers are not alike.** The engine asks an adapter what it supports and
   never assumes Zerodha, MT5 and MT4 behave the same (section 5).
7. **Nothing is silently changed.** An order that breaks a rule is rejected
   with the reason; it is never resized or repriced on the user's behalf.

### Deployment

Development runs from `docker-compose.yml` (verified: see testing.md). For
production the same images run behind a TLS-terminating load balancer with:
a managed PostgreSQL with point-in-time recovery and encrypted storage, a
managed Redis, secrets injected from a secret manager, a static egress address
for broker calls, JSON logs shipped to a central store, Sentry, and separate
scaling for ingest, API and workers. Deployment and disaster-recovery guides are
Phase 7 deliverables.

## 3. Limits found, and what to do instead

These are constraints that the original idea runs into. Each has an alternative
that keeps the product buildable.

| Constraint | Detail | Compliant alternative |
|---|---|---|
| **Zerodha: one static IP per developer account** | Since 1 April 2026 Zerodha rejects API orders unless they come from a static IP registered to that developer account. An IP can be linked to one account and shared only with immediate family. One server therefore cannot place orders for unrelated customers through their own keys. | (a) The customer runs a small execution agent on their own static-IP machine, the same model as the MT4/MT5 bridge. (b) Semi-automatic mode through Kite Publisher, where the customer confirms each order in Zerodha's own screen. (c) Become an exchange-empanelled algo provider through a broker partnership. See [compliance-notes.md](compliance-notes.md). |
| **India's retail algo framework** | Third-party platforms that automate retail orders fall under SEBI's February 2025 framework and the exchanges' standards: broker-controlled API access, algo identification, provider empanelment. | Live Indian execution stays off until a compliance review is signed off. Paper trading is unaffected. |
| **MT4 and MT5 have no cloud API** | Orders can only be placed from inside a terminal. | A bridge Expert Advisor in the customer's terminal polls the platform over HTTPS. No broker password is ever stored. Hosting terminals for customers remains possible later as an opt-in, but needs password storage and Windows servers. |
| **TradingView cannot sign webhooks** | It sends no custom headers and no signature. | An unguessable token in the URL, an optional passphrase in the message, an optional source-IP allowlist, and a signature header for senders that can sign. |
| **TradingView does not retry** | If the endpoint is down or slow, the alert is lost. | Ingest does the minimum and answers fast. A database outage returns an error and the missed alert is visible in TradingView's own log; the platform cannot recover it. |
| **A broker order id is not a fill** | Zerodha's reply only confirms acceptance. | Fills come from postbacks and order-book reconciliation. |

An earlier decision is reversed by the new requirements: MT5 was going to be
"we host the terminals", which means storing account passwords. The rule "do
not store broker login passwords unless a broker-approved architecture
requires it" makes the bridge EA the default for both MT4 and MT5.

## 4. Folder structure

```
trading-platform/
├── backend/
│   ├── app/
│   │   ├── core/            config, db + access context, crypto, passwords, tokens,
│   │   │                    rate limits, email, audit, logging, http middleware   [built]
│   │   ├── models/          SQLAlchemy models                                    [built: identity, audit]
│   │   ├── modules/
│   │   │   ├── auth/        sign-in, sessions, two-factor, passwords             [built]
│   │   │   ├── users/       profile, own activity                                [built]
│   │   │   ├── admin/       user management, audit view                          [built]
│   │   │   ├── system/      health, status                                       [built]
│   │   │   ├── strategies/  webhooks/  signals/                                  [phase 2]
│   │   │   ├── orders/  paper/  brokers/ (interface + simulator)                 [phase 3]
│   │   │   ├── brokers/kite/  brokers/bridge/  instruments/                      [phase 4]
│   │   │   ├── risk/  reconciliation/                                            [phase 5]
│   │   │   └── analytics/  billing/  support/                                    [phase 6]
│   │   ├── ingest/          the webhook service entry point                      [phase 2]
│   │   ├── workers/         Celery app, tasks, sweeper                           [phase 2]
│   │   ├── main.py  cli.py
│   ├── migrations/          Alembic; one migration per schema change             [built: 0001]
│   ├── tests/                                                                    [built: 90 tests]
│   └── Dockerfile  pyproject.toml  requirements.lock
├── frontend/                Next.js app: (auth) pages, (dashboard) pages, components, lib
├── bridge/                  MQL4 and MQL5 Expert Advisors, source + install guides  [phase 4]
├── e2e/                     browser tests against the running stack              [built: phase 1]
├── infra/postgres/          database role bootstrap                              [built]
├── scripts/                 developer helpers                                    [built]
├── docs/
└── docker-compose.yml
```

Each module owns its router, service and schemas. Services take a database
session and plain arguments, so workers and the API share them.

## 5. Broker integration

### One interface

```python
class BrokerAdapter(Protocol):
    capabilities: Capabilities            # what this broker supports; never assumed

    def connect(self) -> None: ...
    def authenticate(self) -> AuthState: ...
    def disconnect(self) -> None: ...
    def get_account_info(self) -> AccountInfo: ...
    def get_balance(self) -> Balance: ...
    def get_positions(self) -> list[Position]: ...
    def get_orders(self, since: datetime) -> list[BrokerOrder]: ...
    def place_order(self, intent: OrderIntent) -> SubmitResult: ...
    def modify_order(self, ref: OrderRef, changes: OrderChanges) -> SubmitResult: ...
    def cancel_order(self, ref: OrderRef) -> SubmitResult: ...
    def close_position(self, ref: PositionRef, quantity: Decimal | None = None) -> SubmitResult: ...
    def close_all_positions(self, symbol: str | None = None) -> list[SubmitResult]: ...
    def get_execution_status(self, client_order_id: str) -> ExecutionStatus: ...
```

Every submission returns one of four outcomes. The engine's retry rule depends
only on this, so no adapter can cause a blind retry:

| Outcome | Meaning | Engine does |
|---|---|---|
| `ACCEPTED` | The broker returned an order id | Move to `SUBMITTED`, wait for fill evidence |
| `REJECTED` | The broker definitely refused | Move to `FAILED` with the broker's reason |
| `NOT_SENT` | Provably never left (connection refused) | Retry with backoff, up to a limit |
| `UNKNOWN` | Sent, no reply | Move to `UNKNOWN`; reconcile by `client_order_id`; never retry blindly |

Every order carries a `client_order_id` the platform generates. It travels to
the broker (Zerodha's `tag`, MetaTrader's magic number plus comment) so an order
whose reply was lost can be found again.

### Two transports

- **Direct adapters** call the broker's API from a worker: Zerodha, the paper
  engine, and the simulated broker used in tests.
- **Bridge adapters** work through an agent the customer runs: the MT4 and MT5
  Expert Advisors, and optionally a Zerodha agent. The agent authenticates with
  a device credential, polls for instructions, claims one, executes it in the
  terminal, and reports the terminal's result, a heartbeat and periodic account
  snapshots. For the engine it is still a `BrokerAdapter`: `place_order` writes
  an instruction, and results arrive as reports.

### Where brokers differ

| | Zerodha Kite | MT5 | MT4 | Paper |
|---|---|---|---|---|
| Size | Quantity, in lot multiples for F&O | Lots, by the symbol's volume step | Lots | Either |
| Positions | Net per product (MIS, CNC, NRML) | Netting or hedging, per account | Hedging, by ticket | Configurable |
| Stop loss / take profit | Not attached to a regular order: a separate SL order or a GTT | Attached to the position | Attached to the ticket | Simulated |
| Acceptance vs fill | Order id means accepted; fills by postback or order book | Return code plus deal | Ticket plus order lookup | Simulated events |
| Session | Customer logs in daily; token ends 6:00 IST | Terminal session | Terminal session | None |
| Market orders | Need market protection | Filling policy varies by symbol | Slippage parameter | Slippage model |
| Lookup key | `tag`, 20 alphanumerics | Magic number and comment | Magic number and comment | Client id |
| Order rate | 10 per second per client | Broker dependent | Broker dependent | None |

### Instruments

TradingView symbols are never assumed to match a broker's. Zerodha's instrument
master (exchange, trading symbol, token, expiry, strike, option type, lot size,
tick size) is synchronised daily. MetaTrader symbol specifications (lot limits,
step, digits, filling modes) are reported by the bridge. Each account has
mapping rules from the alert's symbol to a broker instrument, and an order with
no mapping is rejected, not guessed.

## 6. Execution lifecycle

```
RECEIVED ─► VALIDATED ─► QUEUED ─► EXECUTING ─► SUBMITTED ─► PARTIALLY_FILLED ─► FILLED
    │           │           ▲          │            │                │
    ▼           ▼           │          ▼            ▼                ▼
 REJECTED   REJECTED   AWAITING     UNKNOWN ──► (reconcile)       CANCELLED
 (invalid)  (risk)     APPROVAL     FAILED
```

1. **Receive.** The ingest service authenticates the webhook token, checks size
   and JSON shape, checks the signal's timestamp against the maximum age, and
   inserts it under a unique key of webhook plus signal id. A repeat of the same
   id is acknowledged and never executed again. The signal and its queue entry
   commit in one transaction, then the service answers.
2. **Validate and expand.** A worker resolves the strategy, applies action
   aliases, and creates one order per target account under a unique key of
   signal plus account. Each order goes through instrument mapping, sizing and
   the risk engine. A failure is `REJECTED` with the exact rule. Semi-automatic
   strategies stop at `AWAITING_APPROVAL` until the user approves or the signal
   expires.
3. **Execute.** A worker claims the order with a conditional update that only
   one worker can win, and takes a short lease. It re-checks the kill switches,
   the signal age and the live-trading gate, then calls the adapter.
4. **Confirm.** Postbacks, bridge reports and polling record fills and move the
   order forward. Partial fills are recorded as they arrive.
5. **Reconcile.** A periodic job takes every `UNKNOWN` order and every
   `EXECUTING` order whose lease expired (a worker died mid-submission), asks the
   broker by `client_order_id`, and resolves it. Unresolved orders raise an
   alert and trip the account's circuit breaker. Positions are compared with the
   broker's and differences shown.
6. **Record.** Every transition appends a row with a timestamp, which is also
   how signal-to-submission latency is measured.

Failure behaviour this design gives:

| Event | Result |
|---|---|
| Same webhook delivered twice | Second is acknowledged as a duplicate; one order |
| Two workers pick the same order | One wins the claim; the other does nothing |
| Worker dies after sending, before recording | Lease expires; reconciled with the broker before any retry |
| Broker reply lost | `UNKNOWN`; looked up by `client_order_id`; never re-sent blindly |
| Redis down | Signals still commit; the sweeper dispatches when it returns |
| Database down | Webhook returns an error; nothing is half-recorded |
| One account fails | Other accounts' orders are independent rows and unaffected |
| Kill switch on | Orders stop at the execute step with the reason recorded |

## 7. Regulatory gate

Two settings control live execution, both read at start-up:

- `LIVE_TRADING_ENABLED` defaults to `false`.
- Setting it to `true` without `LIVE_TRADING_COMPLIANCE_APPROVAL` (a reference
  to the signed-off review) stops the service from starting. **Built and tested.**

Paper trading has its own flag and does not depend on these. The dashboard shows
the live-trading state on every page.

Controls the architecture reserves room for, so they can be switched on without
a rebuild: per-broker and per-market enablement, an algo identifier field on
every order, a per-client order-rate limiter, per-account egress routing, market
protection on market orders, and a record of each user's regulatory
acknowledgements. The open questions and sources are in
[compliance-notes.md](compliance-notes.md). Nothing in this repository claims
regulatory approval or broker authorisation.

## 8. Roadmap

| Phase | Scope | Exit criteria | Status |
|---|---|---|---|
| 1 | Architecture, schema, authentication, dashboard layout, tenant isolation | Auth flows and isolation tested against PostgreSQL; dashboard runs against the API; Compose stack starts | **Done** |
| 2 | Strategies, webhook engine, signal validation, durable queue, event log, idempotency | Duplicate, stale, malformed and unauthenticated alerts all rejected by test; signals survive a Redis outage and a restart | Next |
| 3 | Broker interface, simulated broker, paper trading, order state machine | Full lifecycle against the simulator, including partial fills, rejects, timeouts and ambiguous replies | |
| 4 | Zerodha adapter, MT5 and MT4 bridge EAs, instrument master and mapping | Adapters pass the same lifecycle suite; EAs compile and are exercised against a demo terminal by you | |
| 5 | Risk engine, multi-account execution, reconciliation, kill switches | Every limit has a rejecting test; duplicate-worker and crash-mid-execution tests pass | |
| 6 | Analytics, billing, admin dashboard, monitoring, support | Metrics traced to stored fills; expiry blocks new entries but not exits | |
| 7 | Security audit, performance and failure testing, deployment, remaining guides, compliance review | Evidence for each production-readiness claim | |
