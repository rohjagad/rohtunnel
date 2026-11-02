# Architecture

This document is the technical design behind [`PLAN.md`](PLAN.md). It covers
the components, state transitions, storage, panel integration, reliability and
security boundaries.

---

## 1. Design constraints

These are inherited from the existing panel, not preferences:

1. `fn-autosc-api` is **single-threaded**. Its scripts edit shared state
   (`/etc/xray/json/*.json`, `/etc/passwd`, service units) with no locking of
   their own. Two create calls must never overlap.
2. The API has **no idempotency key** and **no get-one-account endpoint**.
   A timeout is ambiguous: the account may exist.
3. Handlers answer **HTTP 200 for both success and domain errors**, with
   `{"status":"success",...}` or `{"status":"error","message":"..."}`.
   HTTP 500 means the handler crashed.
4. The API token is a **root credential**; the handlers run the panel's
   scripts as root.
5. The panel, not the store, deletes accounts past expiry (`xp` cron).
6. The panel can create SSH, VMess, VLESS, Trojan (four transports) and
   NoobzVPN. It has no API for WireGuard, L2TP, SlowDNS, Shadowsocks or Socks5.

The architecture is shaped around these constraints.

---

## 2. Components

| Component | Runs as | Responsibility |
| :-- | :-- | :-- |
| `rohtunnel serve` | `rohtunnel` | public storefront, order pages, admin, webhooks |
| `rohtunnel worker` | `rohtunnel` | claims provision/renew jobs, calls the panel |
| `rohtunnel maintenance` | timer | payments reconciliation, expiry mirror, reminders, stale jobs, backups |
| SQLite | file | orders, payments, jobs, account mirror, audit |
| nginx | root | TLS, static files, proxy, rate limits, admin allowlist |
| `fn-autosc-api` | root, `127.0.0.1:9000` | panel operations |
| payment gateway | external | money, QRIS/e-wallet/VA, signed callbacks |
| SMTP / Telegram | external | notifications |

The web and worker are separate processes but the same Python package and
database. The worker is intentionally not a thread inside the web process:
a web restart must not abort a provisioning call, and a worker crash must not
take the storefront down.

---

## 3. Process model

### Web process

- `ThreadingHTTPServer` from the stdlib, or a small WSGI adapter if the route
  set grows; no framework dependency.
- One SQLite connection per request (or a thread-local connection), WAL mode,
  `busy_timeout=5000`, `foreign_keys=ON`.
- Write transactions are short: webhooks and checkout only. Provisioning is
  the worker's job.
- Static assets are served by nginx in production and by the app in
  development.
- Binds `127.0.0.1:8088` by default.

### Worker process

- Exactly one instance, enforced by:
  1. a `flock` on `/run/rohtunnel/worker.lock`;
  2. an atomic job claim (`BEGIN IMMEDIATE`, select one, update one).
- Handles one job at a time. This is deliberate: the panel API serializes
  anyway, and a single worker makes ordering and reconciliation simple.
- Longest call is the panel create (API handler timeout 180 s); the client
  timeout is 200 s.
- On SIGTERM, stops claiming, finishes or releases the current job, exits.
  systemd `TimeoutStopSec` is set above the client timeout.

### Maintenance

A systemd timer runs every minute (or every five minutes):

- return expired `running` jobs to `queued` (lease timeout);
- reconcile payments that were paid but whose webhook was missed;
- refresh the account mirror from `list-*`;
- enqueue expiry reminders at D-7/D-3/D-1;
- mark expired accounts;
- prune old sessions and idempotency records;
- run the daily SQLite backup.

Maintenance must be safe to run concurrently with the worker. It uses short
transactions and does not perform panel mutations except through jobs.

---

## 4. Data model

The complete DDL is in [`migrations/0001_init.sql`](../migrations/0001_init.sql).
This section explains the shape and the rules the code must preserve.

### 4.1 `panels`

One row per panel. v1 uses one active panel; the table exists so multi-panel
is a future configuration change, not a schema migration.

Fields: `name`, `base_url`, `token_file`, `active`, timestamps. The token
itself is **not** stored; `token_file` points at a `0600` file read by the
process.

### 4.2 `products`

A sellable plan. It stores the panel mapping (`protocol`, `core`), duration,
quota, IP limit, price and description. `protocol`/`core` are constrained so
an impossible combination cannot be inserted.

`price_idr` is an integer; IDR has no minor unit in practice. Prices are
snapshotted into `orders.product_snapshot` so a later price edit cannot change
an existing order.

### 4.3 `orders`

The commercial record:

- `public_id` — customer-facing invoice number, e.g. `FN-20261102-7K3Q9M`.
- `access_token_hash` — SHA-256 of the 32-byte order token. The raw token is
  shown once in the redirect URL and stored in an HttpOnly cookie; it is not
  recoverable from the DB.
- `product_snapshot` — JSON copy of name, protocol, core, duration, quota, IP
  limit, price and terms version.
- customer contact fields (name, email, WhatsApp, Telegram). Only what is
  needed for delivery and support.
- `amount_idr`, `currency`, `payment_provider`, `payment_ref`,
  `payment_status`, `paid_at`.
- `fulfillment_status`, `fulfillment_attempts`, `last_error`,
  `next_attempt_at`.
- `account_expires_at`, `renewal_of_order_id`, `idempotency_key`.
- timestamps.

Separate payment and fulfilment statuses are the most important schema
decision. A paid order can be `needs_review` without looking unpaid, and a
webhook retry cannot overwrite a delivered account.

### 4.4 `accounts`

The store's mirror of the panel account:

- `order_id` unique (one account per order in v1).
- `panel_id`, `protocol`, `core`, `username`.
- `credentials_json` — password, links, account card text, and whatever the
  API returned. Sensitive; see ADR-007.
- `panel_expiry_raw` — exactly what the panel returned (`YY-MM-DD`,
  `YYYY-MM-DD`, or the SSH `chage` string).
- `expires_at` — computed by the store at creation from the order duration;
  used for UI and reminders.
- `status` — `active`, `expired`, `suspended`, `deleted`, `unknown`.
- `last_seen_at` — last successful `list-*` match.

`UNIQUE(panel_id, username)` prevents the store from issuing the same name
twice on one panel. The panel may allow the same name on multiple Xray
transports, but the store does not need that freedom; a global name per panel
keeps support and reconciliation unambiguous.

### 4.5 `payment_events`

Raw webhook records. `UNIQUE(provider, event_id)` is the replay guard. A
row can exist with `order_id` null if it arrived before the local intent was
committed; reconciliation matches it later.

Storing the raw body (or its hash and selected fields) is for audit and
dispute handling. PII in payloads is minimized; the full body is retained
only as long as needed for disputes (configurable).

### 4.6 `order_events`

Append-only timeline. Every state transition writes one row with an actor:

- `system`
- `customer`
- `webhook:<provider>`
- `admin:<id>`
- `worker`

`data_json` carries structured details (panel endpoint, error code, old/new
status). Never store passwords or the panel token here.

### 4.7 `jobs`

A durable queue:

```text
kind          provision | notify | reconcile | expire | remind | payment_sync
status        queued | running | done | failed | cancelled
attempts      integer
max_attempts  integer
run_after     timestamp
locked_at     timestamp
dedupe_key    text, unique per kind
```

`UNIQUE(kind, dedupe_key)` makes enqueueing idempotent. A provision job's
dedupe key is `order:<id>`, so a duplicate webhook cannot create two jobs.

### 4.8 Auth tables

`admin_users` stores a scrypt hash (`scrypt$N$r$p$salt$hash`), role and active
flag. `admin_sessions` stores a SHA-256 hash of a 32-byte session token, the
CSRF token, expiry, IP and user agent. Session tokens are never stored raw.

---

## 5. State transitions

### 5.1 Payment

```text
unpaid ──webhook/admin──> paid
unpaid ──window elapsed──> expired
paid   ──admin───────────> refunded
unpaid ──gateway failure─> failed
```

Rules:

- Only `unpaid -> paid` triggers fulfilment.
- `paid` is terminal for the payment state except `refunded`.
- A webhook for an `expired` order that still has a valid payment is accepted
  if the amount matches and the account can still be created; otherwise it
  becomes `paid` + `needs_review`, not silently dropped.
- `refunded` never reverts to `paid`.

### 5.2 Fulfilment

```text
pending ──job enqueued──> queued
queued  ──claim─────────> provisioning
provisioning ──success──> delivered
provisioning ──transient> retry_wait ──backoff──> provisioning
provisioning ──ambiguous/max──> needs_review
needs_review ──admin retry──> provisioning
any pre-delivered state ──cancel──> cancelled
delivered ──expiry passed──> expired
```

Rules:

- `delivered` requires a stored `accounts` row with credentials.
- `cancelled` is allowed only before `delivered` (or after a refund/delete).
- A job in `running` past its lease is recovered to `queued` with
  `reconcile_first = true`.
- A `needs_review` order never auto-retries; an admin must look at the panel
  evidence and click retry.

### 5.3 Transition table

| From | Event | To | Side effects |
| :-- | :-- | :-- | :-- |
| `draft` | checkout validated | `pending/unpaid` | create order + payment intent |
| `pending/unpaid` | paid webhook | `queued/paid` | enqueue provision, event |
| `pending/unpaid` | admin mark paid | `queued/paid` | same, actor admin |
| `pending/unpaid` | payment window | `pending/expired` | cancel pending payment |
| `queued/paid` | worker claim | `provisioning/paid` | set `locked_at`, attempt++ |
| `provisioning/paid` | API success | `delivered/paid` | store account, notify |
| `provisioning/paid` | transient error | `retry_wait/paid` | schedule `run_after` |
| `provisioning/paid` | ambiguous/max | `needs_review/paid` | alert admin |
| `retry_wait/paid` | backoff elapsed | `provisioning/paid` | claim again |
| `delivered/paid` | expiry passed | `expired/paid` | mark account expired |
| `delivered/paid` | refund approved | `cancelled/refunded` | delete account first |
| `needs_review/paid` | admin retry | `provisioning/paid` | reset attempt budget |
| `needs_review/paid` | admin cancel | `cancelled/paid` | refund decision separate |

---

## 6. Panel client

The client is a small stdlib `urllib.request` wrapper. It does not use
`requests`, so there is no runtime dependency.

```python
class FnPanelClient:
    def __init__(self, base_url, token, timeout=200): ...
    def ping(self) -> dict: ...
    def add_ssh(self, username, password, days, ip_limit) -> dict: ...
    def add_xray(self, protocol, core, username, days, ip_limit, quota_gb) -> dict: ...
    def add_noobz(self, username, password, days) -> dict: ...
    def list_xray(self) -> dict: ...
    def list_ssh(self) -> dict: ...
    def list_noobz(self) -> dict: ...
    def renew_ssh(self, username, days) -> dict: ...
    def renew_xray(self, username, days, core) -> dict: ...
    def delete_ssh(self, username) -> dict: ...
    def delete_xray(self, username) -> dict: ...
    def delete_noobz(self, username) -> dict: ...
```

Rules:

- `Authorization` is the raw token; no `Bearer`.
- `Content-Type: application/json`; body is a JSON object.
- Parse JSON even on HTTP 200; treat `status == "error"` as a domain error.
- HTTP 500 is a crash: read `error`/`stdout`, log a redacted excerpt, classify
  as transient or ambiguous depending on whether the handler could have run.
- Timeout is 200 s; the API's handler timeout is 180 s.
- Never log the token or full request body (bodies contain passwords).
- Validate responses: `status`, `username`, expected fields. A malformed
  success is treated as ambiguous, not as delivered.

The exact endpoint mapping and response shapes are in
[`PANEL-INTEGRATION.md`](PANEL-INTEGRATION.md).

---

## 7. Provisioning algorithm

```text
claim job (order_id, attempt)
load order + product + panel
if order already delivered:
    mark job done; return
if order cancelled/expired:
    mark job cancelled; return

build username + credentials
if attempt > 1 or job.reconcile_first:
    evidence = panel.list_*(username)
    if evidence says account exists:
        if order has stored credentials:
            mark delivered; done
        else:
            delete the account; if delete fails -> needs_review; stop
    # absent -> safe to create

response = panel.create(...)
if response.status == success:
    validate fields; store account; mark delivered; enqueue notifications
elif response is a domain error:
    if "already exists" in message:
        reconcile; either adopt, delete+retry, or needs_review
    elif message is validation/unsupported:
        needs_review (config bug, not transient)
    else:
        retry with backoff
else: # timeout/connection/HTTP 500
    mark attempt; reconcile on next attempt
```

Key rule: **a create is never repeated until the previous attempt has been
classified.** That is what prevents duplicate accounts when the network times
out after the panel already wrote the account.

### Credentials

- SSH: generate a password with `secrets.token_urlsafe` (or a readable
  alphanumeric); store it only after the panel confirms creation. The account
  card text from the API is stored for display.
- Xray: the API/panel generates the UUID. The response's `links` array is the
  source of truth. If the response is lost, the links cannot be rebuilt from
  `list-xray`; the delete-and-recreate path is used.
- NoobzVPN: generate a password; the API returns username/password/expired.

### Expiry

The store computes `expires_at = now + duration_days` and also stores the
panel's raw expiry. The computed value drives reminders; the raw value is
shown for support. A mismatch is flagged in reconciliation, not silently
overwritten.

---

## 8. Payment architecture

### 8.1 Provider interface

The provider adapter is the only code that knows a gateway's payload shape.
The order/fulfilment code sees a normalized `PaymentEvent`.

```python
@dataclass(frozen=True)
class PaymentIntent:
    provider: str
    provider_ref: str
    amount_idr: int
    currency: str
    qr_payload: str | None
    checkout_url: str | None
    expires_at: datetime
    instructions: str

@dataclass(frozen=True)
class PaymentEvent:
    provider: str
    event_id: str
    provider_ref: str
    status: Literal["paid", "expired", "failed", "refunded"]
    amount_idr: int
    paid_at: datetime | None
    raw: dict
```

### 8.2 Webhook transaction

```text
read raw body (size-limited)
verify signature (constant-time compare)
parse event; require event id, ref, status, amount
BEGIN IMMEDIATE
  insert payment_events(provider, event_id) ON CONFLICT DO NOTHING
  if inserted == 0: COMMIT; return 200 (duplicate)
  find order by provider_ref
  if no order: store unmatched; COMMIT; return 200
  if event.status != paid:
      record status; COMMIT; return 200
  if amount != order.amount_idr or currency mismatch:
      mark event rejected; alert; COMMIT; return 200
  if order.payment_status == paid:
      COMMIT; return 200 (already processed)
  update order set payment_status=paid, paid_at=...
  update order set fulfillment_status=queued
  insert job(kind=provision, dedupe_key=order:<id>)
  insert order_event
COMMIT
return 200
```

The response is always 200 for events we have durably recorded, even if the
order is later flagged; gateways retry non-2xx and we do not want a retry
storm for a business decision. Signature failures return 401/400 and are
logged.

### 8.3 Manual provider

`manual` creates an order with bank-transfer/QRIS instructions from settings.
The admin “Mark paid” action calls the same `mark_order_paid` service with
`actor=admin:<id>`, so there is one paid transition implementation.

### 8.4 Refunds

A refund is an admin action:

1. Require the order to be `delivered` (or `paid` before provisioning).
2. If an account exists, call `delete-*` and verify it is gone.
3. Call the provider refund API if automatic; otherwise record a manual refund.
4. Set `payment_status=refunded`, `fulfillment_status=cancelled`.
5. Write events. If the panel delete fails, stop before the refund and leave
   `needs_review`.

The store never auto-refunds on a chargeback; it records the event and alerts
an admin.

---

## 9. Security boundaries

```text
Internet
  │  HTTPS
  ▼
nginx ── static, rate limits, admin allowlist
  │  127.0.0.1:8088
  ▼
RohTunnel web  ── SQLite ── RohTunnel worker
  │                              │ HTTP + token, loopback
  │                              ▼
  │                        fn-autosc-api (root)
  │                              │ runs
  │                              ▼
  │                        panel scripts (root)
  ▼
customer browser / admin browser
```

- The browser never talks to `fn-autosc-api`.
- The web process does not call the panel for provisioning; only the worker
  does. Admin “retry” enqueues a job, it does not call the panel inline.
- The panel token is readable only by the service user (a dedicated copy),
  not by `www-data` and not by the webroot.
- The SQLite file is not under the nginx webroot.
- Admin and customer sessions are separate cookie names and separate token
  tables.
- Order pages are private by capability URL; they are `noindex` and
  `Referrer-Policy: no-referrer`.

### Threat model notes

- **Root-adjacent service.** If an attacker gets code execution in
  RohTunnel, they can read the panel token and control the VPN host. The
  mitigation is a small attack surface, no uploads, no shell execution from
  user input, strict escaping, and keeping the service off the public
  interface except through nginx.
- **Payment webhook is unauthenticated by nature.** Signature verification is
  mandatory; amount and currency are checked against the order. A valid
  signature is not enough if the amount is wrong.
- **Order token in a URL.** URLs leak through Referer, browser history and
  screenshots. Set `no-referrer`, exchange the token for an HttpOnly cookie on
  first view, and never put credentials in the URL itself.
- **Admin account compromise.** Password hashing is scrypt; sessions are
  short-lived and revocable; optional TOTP is a Phase 5 item. Admin actions
  are audited.
- **PII.** Collect only name, email and optional WhatsApp/Telegram. Retention
  is configurable; expired order credentials are redacted after the retention
  window.

---

## 10. Deployment

### 10.1 Same-host topology (recommended)

```text
store.example.com ──nginx──> 127.0.0.1:8088 RohTunnel
                                    │
                                    ├── SQLite /var/lib/rohtunnel
                                    └── worker ──> 127.0.0.1:9000 fn-autosc-api
                                                          │
                                                          └── panel scripts
```

The store does not need a public API port. Only nginx is exposed. The API is
already loopback-only.

### 10.2 Separate-host topology

If the store runs elsewhere, expose the panel API through nginx with HTTPS,
restrict `/api/` by source IP, and use a dedicated token. The store's panel
client then points at `https://vpn.example.com/api`. This is supported by
configuration but is a larger attack surface; prefer same-host.

### 10.3 Files and permissions

```text
/opt/rohtunnel/                        root:root 0755  (code, read-only to service)
/etc/rohtunnel/env                     rohtunnel:rohtunnel 0600
/etc/rohtunnel/panel-token             rohtunnel:rohtunnel 0600
/var/lib/rohtunnel/rohtunnel.sqlite3    rohtunnel:rohtunnel 0600
/var/lib/rohtunnel/                    rohtunnel:rohtunnel 0700
/var/backups/rohtunnel/                rohtunnel:rohtunnel 0700
/run/rohtunnel/                        rohtunnel:rohtunnel 0700
```

### 10.4 systemd

```ini
[Service]
User=rohtunnel
Group=rohtunnel
WorkingDirectory=/opt/rohtunnel
EnvironmentFile=/etc/rohtunnel/env
ExecStart=/usr/bin/python3 -m rohtunnel serve
Restart=on-failure
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/rohtunnel /var/backups/rohtunnel /run/rohtunnel
```

The worker unit is the same with `worker` instead of `serve`, plus a
`TimeoutStopSec` above 200 s. Examples are in [`deploy/`](../deploy).

### 10.5 nginx

- TLS 1.2/1.3, HSTS, modern ciphers.
- `proxy_pass http://127.0.0.1:8088`.
- `client_max_body_size 1m`.
- `proxy_read_timeout 30s` for normal pages; webhooks are short.
- Rate limit zones for `/checkout`, `/order/lookup`, `/webhooks/`.
- Optional `allow <office-ip>; deny all;` on `/admin`.
- `add_header Referrer-Policy no-referrer always;` on order pages.
- Serve `/static/` with `expires 7d`.

### 10.6 Backups and restore

```bash
sqlite3 /var/lib/rohtunnel/rohtunnel.sqlite3 \
  ".backup '/var/backups/rohtunnel/db-$(date +%F).sqlite3'"
```

Then compress and encrypt. Test restore into a temporary directory and run
`rohtunnel doctor --db <restored>` before trusting the backup.

---

## 11. Observability

### Logs

JSON lines to stdout/journald:

```json
{"ts":"2026-11-02T12:00:00Z","level":"info","event":"order.paid",
 "order":"FN-20261102-7K3Q9M","amount":15000,"provider":"tripay",
 "request_id":"..."}
```

Never log: Authorization header, panel token, passwords, full links, full
webhook body (store a hash and selected fields), session tokens.

### Metrics (v1 without Prometheus)

Admin dashboard and `/readyz` are enough at launch. Counters:

- orders created / paid / delivered / failed today;
- paid-not-delivered older than 5 minutes;
- `needs_review` count;
- jobs by status and oldest queued age;
- webhook signature failures;
- panel ping latency;
- DB size and last backup age.

### Alerts

- Any `needs_review` order → admin Telegram/email.
- Paid order with no delivered account after 10 minutes → alert.
- Worker heartbeat older than 2 minutes → alert.
- Webhook signature failure burst → alert.
- Backup failure → alert.

---

## 12. Failure scenarios

| Scenario | Behavior |
| :-- | :-- |
| Web process restarts | worker keeps running; orders unaffected |
| Worker restarts mid-call | job lease expires; maintenance requeues with `reconcile_first`; no blind duplicate |
| Worker host reboots | jobs persist in SQLite; worker recovers on boot |
| Panel API down | jobs retry with backoff; customers see “processing”; admin alerted after threshold |
| Panel API times out after create | next attempt reconciles via `list-*`; adopt or delete+retry |
| Payment webhook lost | maintenance calls provider status API for pending intents; marks paid once |
| Duplicate webhook | unique event row; second call is a no-op |
| Webhook arrives before order commit | stored unmatched; reconciliation matches by provider ref |
| SQLite locked | `busy_timeout`, short transactions; web returns 503 after timeout, never partial state |
| Disk full | readyz fails; no new orders; alert; backup cleanup |
| Panel token rotated | update token file, restart worker/web; in-flight jobs fail and retry |
| Account deleted manually in panel | reconciliation marks `unknown`/`expired`; order page says contact support |
| Customer loses order link | email lookup sends a fresh link, rate-limited |
| Chargeback | payment event recorded; order `needs_review`; no auto-delete |

---

## 13. Code layout (target)

```text
rohtunnel/
  __init__.py
  __main__.py          CLI: initdb, serve, worker, maintenance, doctor
  config.py            environment + secret files
  db.py                connections, migrations, transactions
  models.py            dataclasses / row mapping
  http.py              router, request/response helpers
  web/
    public.py          storefront, checkout, order pages
    admin.py           login, dashboard, orders, products, settings
    webhooks.py        payment callbacks
  services/
    orders.py          create order, mark paid, state transitions
    provisioning.py    worker algorithm and reconciliation
    payments.py        provider interface and registry
    notifications.py   email, Telegram, WhatsApp links
    expiry.py          mirror refresh, reminders
  panel/
    client.py          fn-autosc-api client
    mapping.py         product -> endpoint/body
  payments/
    manual.py
    tripay.py
  templates/           server-rendered HTML
  static/              CSS/JS
migrations/
  0001_init.sql
tests/
deploy/
docs/
```

The walking skeleton currently ships only `config.py`, `db.py`, `server.py`
and `__main__.py`; the rest is added phase by phase.

---

## 14. Why not X?

| Alternative | Why not for v1 |
| :-- | :-- |
| Django/Laravel/Next.js | adds a runtime, dependency tree and upgrade path on a root-adjacent host; the panel API is stdlib Python |
| PostgreSQL/MySQL | another daemon to secure and back up; SQLite WAL handles this order volume |
| Redis/Celery | another service and failure mode; a SQLite job table with atomic claim is enough |
| Stripe | not the usual Indonesian payment mix; gateway adapter keeps the option |
| Calling the panel from the browser | leaks the root token; CORS is not a security boundary |
| Auto-retry creates | duplicates accounts because the API has no idempotency |
| One process for web + worker | a web restart aborts provisioning; a worker crash takes down the store |
