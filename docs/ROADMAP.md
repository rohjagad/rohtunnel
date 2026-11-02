# Roadmap

Issue-sized work, ordered so each phase is independently useful. Checkboxes
are the source of truth for status.

---

## Phase 0 — Plan and skeleton (done)

- [x] Write the product plan and scope.
- [x] Write the architecture and state model.
- [x] Document the `fn-autosc-api` contract and reconciliation algorithm.
- [x] Define the initial SQLite schema.
- [x] Create the repository, README, ADRs and deployment examples.
- [x] Ship a stdlib walking skeleton: `initdb`, `serve`, `doctor`,
      `/healthz`, `/readyz`.
- [x] Add the first smoke tests.

**Exit criteria:** repo pushed; plan reviewed; open questions in
[`PLAN.md`](PLAN.md#17-open-questions) have owners.

---

## Phase 1 — Foundation

- [ ] Config loader with validation and secret-file support (env, 0600 files,
      redacted `doctor` output).
- [ ] Migration runner: forward-only, transactional, `schema_migrations`,
      idempotent re-run, backup-before-migrate hook.
- [ ] DB connection policy: WAL, `busy_timeout`, `foreign_keys`, one
      connection per thread/request.
- [ ] Router and request helpers: methods, path params, JSON/form parsing,
      body size limit, request id, security headers.
- [ ] HTML templates: auto-escaping, layout, storefront, plan page.
- [ ] Admin auth: scrypt, sessions, CSRF, login rate limit, logout.
- [ ] Product CRUD: validation for protocol/core combinations, active flag,
      sort order, price in IDR.
- [ ] Admin panel CRUD + panel ping/health.
- [ ] Seed script for a first panel and sample products (no fake prices in
      production; seed is explicit).
- [ ] Structured logging with redaction tests.
- [ ] CI: Python 3.11/3.12, unittest, compileall, schema check.

**Exit criteria:** a fresh `initdb` + `serve` renders the catalog from SQLite;
an admin can log in, create a product and a panel, and log out; tests pass.

---

## Phase 2 — Manual commerce and provisioning

- [ ] Checkout service: validate product, username, customer, terms; snapshot
      price; create order with public id and order token.
- [ ] Order access: token query → HttpOnly cookie exchange; `/order/{id}`
      renders status; `noindex` + `no-referrer`.
- [ ] Manual payment provider: instructions, payment window, expiry.
- [ ] Admin order list/detail/timeline; “mark paid”; “retry”; “cancel”.
- [ ] `mark_order_paid` service with a single transition path (webhook and
      admin use it).
- [ ] Jobs table + atomic claim + worker lock + graceful shutdown.
- [ ] Panel client: ping, add SSH, add Xray (one transport first), list,
      delete; response validation.
- [ ] Provisioning service: create, success path, domain-error path,
      ambiguous timeout path, retry/backoff, `needs_review`.
- [ ] Account mirror and credentials storage.
- [ ] Order page displays account card/links/password with copy buttons.
- [ ] Email notification (SMTP) with private order link.
- [ ] Admin alert on `needs_review`.
- [ ] Tests: fake panel server, success/error/timeout/duplicate, worker
      restart.

**Exit criteria:** an admin marks a manual order paid; the worker creates a
real SSH or Xray account on staging; the customer page shows credentials; a
forced timeout does not create a duplicate.

---

## Phase 3 — Automatic payments

- [ ] Payment provider interface + registry (`manual`, `tripay`).
- [ ] Tripay adapter: create closed payment (QRIS/e-wallet/VA), signed
      callback, refund.
- [ ] Webhook endpoint: raw-body signature, size limit, unique event id,
      amount/currency validation, fast 200.
- [ ] Unmatched-event reconciliation (webhook before order commit).
- [ ] Payment status polling for pending intents (missed webhook).
- [ ] Admin payment events view and manual reconciliation.
- [ ] Refund flow: delete account first, then provider refund.
- [ ] Tests: forged signature, replay, wrong amount, duplicate event,
      out-of-order event, expired order paid late.

**Exit criteria:** a sandbox QRIS payment automatically queues and delivers;
a replayed webhook is a no-op; a wrong-amount webhook is rejected and alerting
fires.

---

## Phase 4 — Full catalog, renewal and expiry

- [ ] All Xray transports (`ws`, `http`, `xhttp`, `grpc`) and all three
      protocols.
- [ ] NoobzVPN create/delete; mark non-renewable in the UI.
- [ ] Renewal order flow: link to original order, renew endpoint, expiry
      update, receipt.
- [ ] Expiry mirror refresh from `list-*`; handle manually deleted accounts.
- [ ] Reminder jobs at D-7/D-3/D-1 with dedupe.
- [ ] Expired account page: renewal CTA or new-order CTA.
- [ ] Username reservation/collision policy (generated suffix, event).
- [ ] Account card/QR rendering per protocol.
- [ ] Tests per product matrix; contract test against staging panel.

**Exit criteria:** every listed product passes create → deliver → renew →
delete on staging; reminders fire once; a manually deleted account is detected
and shown correctly.

---

## Phase 5 — Hardening and deployment

- [ ] Rate limits: checkout, lookup, webhook, admin login (nginx + app).
- [ ] CSP, HSTS, cookie flags, security headers, `no-referrer` on order pages.
- [ ] Secret management: dedicated panel token, 0600 files, rotation runbook.
- [ ] Application-level encryption for `credentials_json` (optional
      `cryptography` dependency or documented filesystem-only mode).
- [ ] Backups: `.backup`, compress, encrypt, off-host, retention, restore
      drill.
- [ ] systemd units, nginx config, log rotation, graceful stop.
- [ ] `/readyz` panel ping + DB writable; admin dashboard health cards.
- [ ] Alerts: paid-not-delivered, needs-review, worker heartbeat, webhook
      failures, backup failure.
- [ ] Load test: 100 orders, one worker, measure drain time and DB locks.
- [ ] Runbooks: deploy, rollback, token rotation, restore, panel outage,
      stuck job, chargeback.
- [ ] Security review checklist and dependency audit (none at runtime).

**Exit criteria:** fresh deploy under 30 minutes; restore drill passes; load
test drains 100 orders with zero duplicates/losses; alerts reach the operator.

---

## Phase 6 — Growth (post-v1)

- [ ] Multi-panel routing (capacity/weight) and per-panel tokens.
- [ ] Coupons and discounts.
- [ ] Customer accounts / magic-link login.
- [ ] WhatsApp Business API or a provider adapter.
- [ ] Reseller API with scoped keys and rate limits.
- [ ] Additional payment adapters (Midtrans, Xendit).
- [ ] WireGuard/L2TP/SlowDNS products once `fn-autosc-api` supports them.
- [ ] Metrics endpoint (Prometheus) and Grafana dashboard.

---

## First ten issues

If the work is split across contributors, start here:

1. Config loader + `doctor` redaction.
2. Transactional migration runner + backup hook.
3. Admin auth (scrypt + sessions + CSRF).
4. Product CRUD + validation.
5. Storefront templates + catalog page.
6. Checkout + order token.
7. Manual payment + admin mark-paid.
8. Jobs + worker + lock.
9. Panel client + fake panel tests.
10. Provisioning happy path + ambiguous timeout.

Each issue should include its acceptance test in the PR description. A phase
is not done until its exit criteria are demonstrated on staging, not only in
unit tests.

---

## Dependencies between phases

```text
Phase 0 ──> Phase 1 ──> Phase 2 ──> Phase 3
                             │
                             └──> Phase 4 ──> Phase 5 ──> Phase 6
```

Phase 3 can start in parallel with Phase 2 after the order/payment model is
stable, but automatic payments should not ship before manual provisioning is
proven. Phase 4 depends on the worker and reconciliation from Phase 2.

---

## Cross-repo work

Track these in `fn-autosc-api`, not here:

- Idempotency key on create endpoints (P1).
- `GET /account-xray` / `GET /account-ssh` with links (P1).
- Structured `list-ssh` / `list-noobz` (P2).
- NoobzVPN renew endpoint (P2).
- WireGuard/L2TP/SlowDNS handlers (P3).

The store's recovery path is safe without them, but they remove the
delete-and-recreate step and make the integration cheaper to operate.
