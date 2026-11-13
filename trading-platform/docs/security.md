# Security

This is a financial application. The rules below are the ones the code enforces
today; each has a test unless marked otherwise. Items for later phases are
listed at the end.

## What is being protected

In priority order: a user's money (no order they did not intend), their broker
credentials, their trading data, and their account. The main threats considered:
one tenant reading or changing another's data, account takeover, forged or
replayed trade instructions, a staff member or a compromised admin account
overreaching, secrets leaking through logs, and a single bug bypassing a single
control.

## Tenant isolation

Three independent layers. A mistake in one does not expose data.

1. **Queries.** Service code filters by the owning user.
2. **Row-level security.** PostgreSQL applies the same rule again. Each
   transaction declares who it acts for; with no declaration it sees nothing.
   The setting is scoped to the transaction, so it cannot leak through the
   connection pool. Policies are forced, so they bind the table owner too.
3. **Privileges and triggers.** The application's database role is not a
   superuser, owns no tables, cannot bypass row-level security, and can update
   only named columns. Triggers guard role and status changes, session
   tampering and the audit log.

Structural tests keep this true as the schema grows: every table must have
forced row-level security and a policy; every table with a `user_id` must
filter on it; the all-seeing `system` context may be used only in a named list
of files; and every API route must refuse an anonymous request unless it is on
an explicit public list.

The tests run raw SQL as the application role, bypassing the application's own
filters, to prove the database refuses cross-tenant reads, writes and inserts.

## Accounts and sign-in

| Control | Detail |
|---|---|
| Passwords | Argon2id. At least 10 characters, at most 128, not a common password, not the email or product name. Rehashed on sign-in if parameters were raised. |
| No account discovery | Registration, password reset and resend answer the same way whether or not the address has an account. Sign-in and registration also take the same time for unknown and known addresses. |
| Email confirmation | Required before sign-in. Links are single-use, expire in 24 hours, and are stored only as a hash. |
| Throttling | Five failed sign-ins lock that address for 15 minutes, identically for addresses with no account. Per-IP limits on every unauthenticated endpoint that changes anything. |
| Two-factor | TOTP. The secret is encrypted at rest. A code cannot be reused. Five wrong codes end the pending session. Ten single-use recovery codes, stored hashed. |
| Second step is real | After the password, the session can only finish or abandon sign-in. The token is rotated when the second factor succeeds. |
| Password reset | Single-use link, 1 hour. Signs out every device. Does not remove two-factor. |
| Sensitive changes | Changing the password or two-factor asks for the password again, with its own throttle. |
| Suspension | Ends every session at once and blocks sign-in. |

## Sessions and browser security

- The session is a random token in an `HttpOnly`, `SameSite=Lax` cookie. In
  production it is `Secure` and carries the `__Host-` prefix. The database
  stores only a hash, so a database leak does not yield usable sessions.
- Sessions end after 24 hours idle or 7 days in total, and can be listed and
  ended per device. A cookie that no longer maps to a live session is cleared.
- **CSRF.** Every state-changing request from a session must carry a token that
  is an HMAC of the session token. In addition, any state-changing request whose
  `Origin` (or `Referer`) is not the dashboard is refused.
- **Headers.** Content Security Policy with no third-party sources,
  `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: no-referrer`, HSTS on
  the API. Tokens arriving in email links are removed from the address bar.
- The browser talks to one origin. The dashboard forwards `/api` to the backend,
  so the cookie is first-party and CORS is not enabled at all.

## Roles and staff

| Role | Can do |
|---|---|
| User | Their own account and data only |
| Support admin | View users and audit logs; suspend, reinstate and sign out regular users |
| Super admin | All of the above for any account except their own, plus change roles |

- The admin API only opens for a session that signed in with two-factor.
- Staff cannot change their own role or status, and cannot create sessions.
- The staff database context cannot read credential tables at all.
- Every staff action, including viewing a customer's account, is written to the
  audit log with the staff member as actor. Users see that staff acted on their
  account, without the staff member's identity.
- There are no default accounts. The first super admin is created from the
  server with `python -m app.cli create-admin`.

## Secrets and encryption

- Secrets come from the environment (a secret manager in production). The
  service refuses to start without them, and refuses unsafe production settings.
- Field encryption is AES-256-GCM with a key ring: each value records which key
  sealed it, so keys rotate without downtime. Each value is bound to its row, so
  a ciphertext copied to another user's row fails to decrypt.
- TLS terminates at the edge; database and Redis connections must use TLS in
  production (deployment guide, Phase 7).
- Disk-level encryption at rest is the managed database's job; field encryption
  covers the secrets that matter if a backup leaks.

## Logging and audit

- Logs are JSON with a request id. A filter masks anything shaped like a
  password, token, key or authorization header. Access logs are off because
  URLs can carry single-use tokens.
- Audit entries refuse detail keys that look like secrets.
- A test runs every credential-bearing flow and then searches the logs and the
  audit table for each secret used.
- Validation errors name the field and never echo the submitted value.
- Sentry is configured to send no cookies, headers, addresses or request bodies.

## Availability

- Rate limits are shared through Redis. If Redis is down they fall back to
  per-instance counters, so sign-in neither fails nor becomes unlimited.
- Readiness reports `degraded` without Redis and `unavailable` without the
  database.

## Supply chain

- Python dependencies are pinned in `requirements.lock`; Node dependencies in
  `package-lock.json`. Packages needed only at build time are kept out of the
  runtime set.
- CI runs `pip-audit` and `npm audit` on every push.
- Container images run as an unprivileged user; the API container has a
  read-only filesystem and no Linux capabilities.

## Phase 1 review

Checked on 13 November 2026:

| Check | Result |
|---|---|
| Backend tests | 90 pass against PostgreSQL 16 |
| Browser test | 28 checks pass against the production build |
| `pip-audit` on runtime dependencies | No known vulnerabilities |
| `npm audit` on runtime dependencies | No known vulnerabilities |
| `npm audit` including build tooling | 13 findings (11 moderate, 2 low), all in build-time tools that never ship |
| Secrets in the repository | None; `.env` files are git-ignored and only examples are committed |

## Not done yet

These are known gaps, not oversights. None blocks Phase 2.

| Gap | Planned |
|---|---|
| Content Security Policy still allows inline scripts (the framework needs them without nonces) | Nonce-based policy, Phase 7 |
| Changing the account email | Needs its own confirm-both-addresses flow |
| Passkeys / WebAuthn as a stronger second factor | After launch |
| Checking new passwords against a breached-password service | Phase 7 |
| Emails sent after the response are lost if the process dies in between (the user can request another) | Durable outbox with the Phase 2 queue |
| Key management through a KMS with envelope encryption | Deployment, Phase 7 |
| Removing unconfirmed accounts and expired sessions and tokens on a schedule | With the Phase 2 scheduler |
| Webhook credentials, signal replay protection, bridge device credentials | Phases 2 and 4 |
| Penetration test and external review | Before any live trading |
