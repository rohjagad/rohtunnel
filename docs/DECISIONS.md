# Architecture decision records

Short records for choices that are expensive to reverse. Each has a status,
context, decision and consequences.

---

## ADR-001 — Python 3.11+ standard library and SQLite

**Status:** accepted

**Context:** The store runs next to a root-adjacent panel. `fn-autosc-api` is
already Python stdlib + shell, and the host already has Python 3. The order
volume is small (tens to low hundreds per day). Every added runtime is another
thing to patch on a privileged host.

**Decision:** Implement the web app, worker and maintenance commands in Python
3.11+ using only the standard library. Use SQLite in WAL mode for storage. No
Django, Flask, Node, Redis, PostgreSQL or ORM.

**Consequences:**

- Zero runtime dependencies to audit or install.
- We implement routing, templates, sessions and a job queue ourselves; they
  must stay small and well tested.
- SQLite supports one writer at a time. That is acceptable with short
  transactions and a single provisioning worker.
- If the store outgrows this (multi-panel, thousands of orders/day), the
  service boundaries (web/worker/DB) allow a later move to PostgreSQL and a
  framework without rewriting the panel integration.

---

## ADR-002 — Server-rendered HTML, no frontend build step

**Status:** accepted

**Context:** The storefront is a handful of pages. A build toolchain adds
Node, lockfiles and a deploy step for little benefit.

**Decision:** Render HTML on the server. Use small vanilla JS files for copy
buttons, countdowns and QR display. No React/Vue, no bundler, no CDN assets.

**Consequences:**

- Fast first paint and no hydration failures.
- No third-party scripts on order pages; a strict CSP is practical.
- Interactive UI is limited; that is acceptable for v1.

---

## ADR-003 — Single panel at launch, multi-panel-ready schema

**Status:** accepted

**Context:** The operator has one FN AutoSC panel today. Multi-panel routing
adds capacity, failover and per-panel token complexity.

**Decision:** Every product/order/account row carries a `panel_id`, and
`panels` is a table. v1 has one active panel and the admin UI edits that one.
The worker still resolves the panel through the row, not a global constant.

**Consequences:**

- Phase 6 can add routing without a schema rewrite.
- The `panel_id` joins and token resolution must be honoured from day one;
  no “just use the env URL” shortcuts in provisioning code.

---

## ADR-004 — Separate payment and fulfilment states

**Status:** accepted

**Context:** A single status (`pending/paid/done`) conflates money and
delivery. A paid order whose provision failed looks unpaid, and a webhook
retry can overwrite a delivered account.

**Decision:** `orders.payment_status` and `orders.fulfillment_status` are
separate columns with separate transition rules. Only `payment_status=paid`
enqueues provisioning; only stored credentials make `fulfillment_status`
`delivered`.

**Consequences:**

- The order page can say “paid, processing” honestly.
- Admin filters can find paid-not-delivered orders directly.
- Every transition service must update both fields deliberately and write an
  `order_events` row.

---

## ADR-005 — One provisioning worker, SQLite job queue

**Status:** accepted

**Context:** `fn-autosc-api` is single-threaded and its panel scripts mutate
shared state without locking. Running parallel create calls is unsafe. A
broker (Redis/Celery) adds a daemon and another failure mode.

**Decision:** A durable `jobs` table with an atomic claim
(`BEGIN IMMEDIATE` + `UPDATE ... WHERE status='queued'`). Exactly one worker
process, enforced by `flock` and by systemd. Maintenance can requeue stale
`running` jobs with `reconcile_first=true`.

**Consequences:**

- Throughput is bounded by one panel call at a time; acceptable for v1.
- A stuck call delays the queue; the 200 s client timeout bounds it.
- Scaling means adding panels or sharding by panel, not adding workers to the
  same panel.
- No external queue service to secure or back up.

---

## ADR-006 — The panel is the account source of truth

**Status:** accepted

**Context:** The store could keep its own credentials and never read the
panel, but the panel's `xp` cron deletes expired accounts and operators can
edit accounts manually.

**Decision:** `accounts` is a mirror. `list-*` and the create/renew responses
are authoritative. A daily reconciliation updates expiry/status, marks
missing accounts, and reports orphaned panel accounts without deleting them.

**Consequences:**

- The store can show a stale account for at most one reconciliation interval.
- The store never deletes an account just because it is missing from its DB.
- Support has one place (the panel) to verify the real state.

---

## ADR-007 — Credentials live in SQLite with filesystem protection in v1

**Status:** accepted; hardening follow-up

**Context:** `accounts.credentials_json` contains VPN passwords and links.
Python stdlib has no modern AEAD. Adding `cryptography` is a native dependency
on a privileged host. Full-disk encryption may or may not be present.

**Decision:** v1 stores credentials in the SQLite file, which is `0600` and
owned by the service user. Backups are encrypted before leaving the host.
Application-level encryption (envelope encryption with a key file) is a Phase
5 hardening item, not a launch blocker.

**Consequences:**

- Anyone who can read the DB as the service user can read credentials. The
  same person can read the panel token, so the marginal risk is limited.
- The retention policy must redact credentials after the configured window.
- Backups must never be stored unencrypted off-host.
- If `cryptography` becomes acceptable, the schema has room for a
  `credentials_enc` blob and a key version.

---

## ADR-008 — Provider-neutral payments, Tripay first

**Status:** proposed (open question: gateway choice)

**Context:** The target market is Indonesian; QRIS/e-wallet/VA are expected.
Tripay, Midtrans and Xendit all work, and the operator may already have a
merchant account with one.

**Decision:** Define a `PaymentProvider` interface with normalized
`PaymentIntent`/`PaymentEvent`. Ship `manual` first. Implement Tripay as the
first automatic adapter unless the operator already has another gateway; the
adapter is swappable.

**Consequences:**

- No gateway SDK is required; HTTP + signature verification in stdlib.
- Each adapter must be tested with forged/replayed/wrong-amount events.
- The order model stores `payment_provider` and `payment_ref`, so multiple
  providers can coexist later.

---

## ADR-009 — Guest checkout with capability URLs

**Status:** accepted

**Context:** Requiring an account before buying adds friction and a password
reset flow. Customers still need to return to their order.

**Decision:** Guest checkout. Each order has a 32-byte access token; the
customer gets a private link. The token is exchanged for an HttpOnly cookie on
first view and stored only as a SHA-256 hash. Email + invoice lookup sends a
fresh link, rate-limited.

**Consequences:**

- No customer password database in v1.
- The link is a bearer secret; `no-referrer`, no third-party assets, and a
  short-lived cookie reduce leakage.
- A lost link can be recovered by email; if the customer used a fake email,
  support must verify the order manually.
- Customer accounts are a Phase 6 feature, not a prerequisite.

---

## ADR-010 — Do not sell what the panel cannot automate

**Status:** accepted

**Context:** It is tempting to list WireGuard, L2TP or SlowDNS because the
panel has menus for them. But `fn-autosc-api` has no handlers for those, so
every sale would become a manual panel operation.

**Decision:** The catalog is limited to SSH, VMess/VLESS/Trojan (four
transports) and NoobzVPN. A new product type requires an API handler first,
then a product mapping and tests here.

**Consequences:**

- The store's promise (“no operator touch”) stays true.
- Adding WireGuard later is a cross-repo project, not a product-row insert.
- The admin UI must reject unsupported protocol/core combinations at the
  database and form levels.

---

## ADR-011 — UTC in storage, Asia/Jakarta in presentation

**Status:** accepted

**Context:** The panel and customers are in Indonesia, but timestamps must be
unambiguous across logs, backups and future hosting changes.

**Decision:** Store all timestamps as UTC ISO-8601 (`...Z`). Render in the
configured timezone (`ROHTUNNEL_TIMEZONE`, default `Asia/Jakarta`). Expiry
math uses UTC; day-boundary reminders use the display timezone.

**Consequences:**

- No ambiguous local-time rows in the DB.
- Date-only panel expiry strings are parsed as dates, not instants, and the
  store's computed `expires_at` remains the reminder source.
- Tests must cover the UTC/display boundary.

---

## ADR-012 — Forward-only migrations, backup before migrate

**Status:** accepted

**Context:** SQLite schema changes are easy to get wrong, and a failed
migration can take the store offline.

**Decision:** Numbered SQL migrations applied in order, recorded in
`schema_migrations`, executed in a transaction. No down migrations in v1.
`rohtunnel migrate` takes a `.backup` snapshot before applying and refuses
to run if the snapshot fails.

**Consequences:**

- Rollback is “restore the snapshot”, not “run a down script”.
- Every migration must be additive or explicitly rewrite data in one
  transaction.
- CI applies all migrations to an empty DB and re-runs them to prove
  idempotence.

---

## ADR-013 — Account credentials are delivered by the order page first

**Status:** accepted

**Context:** Email is convenient but is not a secure channel for permanent
credentials, and SMTP can be down.

**Decision:** The order page is the source of truth for delivery. Email
contains the private order link and a short summary, not the full credential
set. Telegram/WhatsApp are notifications with a link, not credential dumps.

**Consequences:**

- A customer can always retrieve credentials while the store is up.
- Email deliverability problems do not block delivery.
- The order page's access token becomes the critical secret; see ADR-009.
- Support can resend the link without handling credentials in chat.

---

## ADR-014 — API idempotency is a cross-repo improvement, not a store hack

**Status:** proposed

**Context:** The store can work around the missing idempotency key with
`list-*` + delete/recreate, but that is more complex and briefly deletes an
account.

**Decision:** Keep the workaround in the store, and propose an
`Idempotency-Key` header plus a `get-account` endpoint in `fn-autosc-api`.
Do not add a fake “idempotency” layer that only caches responses locally; it
cannot know whether the panel applied the call.

**Consequences:**

- The store is safe today, at the cost of one extra delete/recreate in a rare
  timeout path.
- When the API lands the header, the store adopts the response directly and
  the delete/recreate path becomes a fallback for old panels.
- The API change must be documented in `fn-autosc/project-information/fn-api.md`
  and covered by its own tests first.
