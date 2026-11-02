# fn-webstore

A self-hosted storefront and order-automation layer for VPN plans running on
[FN AutoSC](https://github.com/rohjagad/fn-autosc).

Customers choose a plan, pay, and receive working credentials without an
operator opening the panel. The store owns orders, payments, fulfilment state,
delivery and renewals. The panel stays the source of truth for the accounts
themselves, and [`fn-autosc-api`](https://github.com/rohjagad/fn-autosc-api) is
the only component allowed to create or change them.

```text
customer ──> fn-webstore checkout ──> payment gateway
                    ▲                        │
                    │                  signed webhook
                    │                        ▼
              order page  <──  provisioning worker  ──> fn-autosc-api
                    ▲                                        │
                    └──────────── credentials ───────────────┘
                                                             ▼
                                                      FN AutoSC panel
```

## Status

**Phase 0 — plan, architecture, schema and walking skeleton.**

The repository currently contains the product plan, technical design, database
schema and a minimal runnable service with `initdb`, `serve`, `doctor` and
health checks. It does **not** process payments or create VPN accounts yet.
Implementation phases are in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## What v1 will sell

| Product | Panel API | v1 |
| :-- | :-- | :--: |
| SSH tunnelling account | `addssh` | yes |
| VMess / VLESS / Trojan over `ws`, `http`, `xhttp`, `grpc` | `add-vmess`, `add-vless`, `add-trojan` | yes |
| NoobzVPN account | `add-noobz` | yes |
| WireGuard, L2TP, SlowDNS | no API endpoint | no |
| Shadowsocks, Socks5 | no backend in the panel | no |
| OpenVPN-only plan | no dedicated API endpoint | no |

A product is only listed if `fn-autosc-api` can create, renew and delete it.
Selling something the panel cannot automate would turn into manual work again.

## The important invariants

1. **A paid order is never lost.** Payment confirmation and enqueueing happen
   in one database transaction.
2. **One provisioning worker.** The panel API is single-threaded and its
   scripts mutate shared files; calls must never race.
3. **The panel is the account source of truth.** The store keeps a read-only
   mirror for customer pages and renewals, and reconciles it regularly.
4. **Ambiguous results are not blindly retried.** A timeout after a create
   call is reconciled against `list-*` before taking another action.
5. **The panel token is a root credential.** It never reaches the browser, is
   never logged, and the service binds to loopback behind nginx.
6. **No card data.** Payment details stay with the gateway; the store only
   stores provider references and statuses.

## Quick start (walking skeleton)

Requirements: Python 3.11+ and SQLite (bundled with Python). No runtime
dependencies.

```bash
python3 -m fn_webstore initdb
python3 -m fn_webstore doctor
python3 -m fn_webstore serve
```

In another terminal:

```bash
curl -s http://127.0.0.1:8088/healthz
curl -s http://127.0.0.1:8088/readyz
```

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

Configuration is environment based; see [`.env.example`](.env.example). The
default database is `./var/webstore.sqlite3` and the default bind is
`127.0.0.1:8088`.

## Repository layout

| Path | Purpose |
| :-- | :-- |
| [`docs/PLAN.md`](docs/PLAN.md) | product plan: users, scope, flows, phases, risks |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | components, state machine, reliability, security, deployment |
| [`docs/PANEL-INTEGRATION.md`](docs/PANEL-INTEGRATION.md) | exact `fn-autosc-api` contract and reconciliation algorithm |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | issue-sized implementation phases and acceptance criteria |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | architecture decision records |
| [`migrations/0001_init.sql`](migrations/0001_init.sql) | initial SQLite schema |
| [`fn_webstore/`](fn_webstore) | Python package (walking skeleton) |
| [`deploy/`](deploy) | nginx and systemd examples |
| [`tests/`](tests) | standard-library test suite |

## References

- [`rohjagad/fn-autosc`](https://github.com/rohjagad/fn-autosc) — the panel.
- [`rohjagad/fn-autosc-api`](https://github.com/rohjagad/fn-autosc-api) — the
  API this store depends on.
- [`rohjagad/FN-API`](https://github.com/rohjagad/FN-API) — the original
  reference package; not fetched or modified.
- [`fn-autosc/project-information/fn-api.md`](https://github.com/rohjagad/fn-autosc/blob/main/project-information/fn-api.md)
  — the API reference written for a future web UI.

## License

No license has been chosen yet. Treat the repository as all rights reserved
until one is added.
