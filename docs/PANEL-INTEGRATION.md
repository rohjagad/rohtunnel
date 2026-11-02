# Panel integration contract

The store is a client of [`rohjagad/fn-autosc-api`](https://github.com/rohjagad/fn-autosc-api).
This document is the integration contract the store is built against. It is
deliberately explicit: the API is root-adjacent, has no idempotency key, and
reports domain errors with HTTP 200, so guessing is dangerous.

---

## 1. Transport

| Item | Value |
| :-- | :-- |
| Default base URL | `http://127.0.0.1:9000` |
| Through nginx | `https://vpn.example.com/api` |
| Auth | raw `Authorization` header must equal a token in `/etc/xray/.key` |
| Prefix | none; no `Bearer` |
| Request | JSON object body, `Content-Type: application/json` |
| Response | JSON object, `Content-Type: application/json` |
| Success | `{"status":"success", ...}` |
| Domain error | `{"status":"error","message":"..."}` with HTTP 200 |
| Crash | HTTP 500 with `{"error":"...","stdout":"..."}` |
| Handler timeout | 180 seconds in the API server |
| Store timeout | 200 seconds |
| Concurrency | serialized: one request at a time in the API server |

The store never sends a request from a browser and never exposes the token.

---

## 2. Endpoints used by v1

| Store action | Method | Path | Body |
| :-- | :-- | :-- | :-- |
| Health | GET | `/ping` | — |
| Create SSH | POST | `/addssh` | `username`, `password`, `expired`, `limit-ip` |
| Create VMess | POST | `/add-vmess` | `username`, `core`, `expired`, `limit-ip`, `quota` |
| Create VLESS | POST | `/add-vless` | same |
| Create Trojan | POST | `/add-trojan` | same |
| Create NoobzVPN | POST | `/add-noobz` | `username`, `password`, `expired` |
| List Xray | GET | `/list-xray` | — |
| List SSH | GET | `/list-ssh` | — |
| List NoobzVPN | GET | `/list-noobz` | — |
| Renew SSH | POST | `/renew-ssh` | `username`, `days` |
| Renew Xray | POST | `/renew-xray` | `username`, `days`, `core` |
| Delete SSH | DELETE | `/delete-ssh` | `username` |
| Delete Xray | DELETE | `/delete-xray` | `username` |
| Delete NoobzVPN | DELETE | `/delete-noobz` | `username` |
| Online SSH | GET | `/cek-ssh` | — |
| Online Xray | GET | `/cek-xray` | — |

`core` values are `ws`, `http`, `xhttp`, `grpc`. `split` is accepted by the API
as a legacy alias for `xhttp`; the store uses `xhttp` only.

`expired` and `days` are positive integers. The panel rejects `0`; the store
must reject it before calling.

---

## 3. Create payloads and responses

### 3.1 SSH

Request:

```json
{"username":"alice","password":"s3cret","expired":30,"limit-ip":2}
```

Success:

```json
{
  "status": "success",
  "username": "alice",
  "password": "s3cret",
  "expired": "Oct 02, 2026",
  "text": "…the account card…"
}
```

Notes:

- The handler mirrors the panel's username rule
  `^[a-z][a-z0-9_]{0,31}$`.
- `expired` is whatever `chage -l` reports, not a canonical date. The store
  also computes `expires_at` from the order duration.
- `text` may contain ANSI-free account card text; store it for display but do
  not parse credentials out of it.
- The SSH account is for TCP forwarding; the panel creates it without a home
  directory and with `/bin/false`.

### 3.2 Xray (VMess / VLESS / Trojan)

Request:

```json
{
  "username": "alice",
  "core": "ws",
  "expired": 30,
  "limit-ip": 2,
  "quota": 50
}
```

Success:

```json
{
  "status": "success",
  "username": "alice",
  "protocol": "vmess",
  "core": "ws",
  "expired": "26-11-02",
  "links": ["vmess://…"]
}
```

Notes:

- `quota` is in GB in the panel's input; the API passes it through to the
  panel's add script.
- `expired` is the panel marker date, typically `YY-MM-DD`.
- `links` is the only source of the generated UUID/links. `list-xray` does not
  return them. This is why a lost create response needs delete-and-recreate.
- Duplicate usernames are refused per transport, not globally.

### 3.3 NoobzVPN

Request:

```json
{"username":"alice","password":"s3cret","expired":30}
```

Success:

```json
{"status":"success","username":"alice","password":"s3cret","expired":"30"}
```

Notes:

- Username rule: `^[a-zA-Z0-9_]{1,16}$`.
- `expired` is the number of days, not a date, in this response.
- There is no renew endpoint. A renewal is a new account; the store must label
  NoobzVPN as non-renewable in v1.

---

## 4. List responses

### 4.1 Xray

```json
{
  "status": "success",
  "count": 3,
  "accounts": [
    {"username": "alice", "expired": "26-11-02", "transport": "ws"},
    {"username": "bob", "expired": "26-12-01", "transport": "grpc"}
  ]
}
```

`transport` is one of `ws`, `http`, `xhttp`, `grpc` (the create-side names).

### 4.2 SSH and NoobzVPN

Both return a text body:

```json
{"status":"success","text":"…panel output…"}
```

The store keeps the text for support and extracts usernames conservatively.
It does not treat a substring match as proof of ownership; the exact username
must appear as a whole word/field.

### 4.3 Contract test

When the panel client lands in Phase 2, `doctor` and the worker startup check
call `/ping` and expect `{"status":"success"}`. A panel that fails ping is
unhealthy; the store still serves cached order pages but does not claim new
provisioning jobs. In Phase 0 `doctor` checks the token file and database
only, because the client is not implemented yet.

---

## 5. Error classification

| Observation | Classification | Action |
| :-- | :-- | :-- |
| HTTP 200, `status=success`, expected fields | success | store account |
| HTTP 200, `status=error`, message contains `already exists` | ambiguous | reconcile |
| HTTP 200, `status=error`, message contains `no such account` on delete | already gone | treat delete as success |
| HTTP 200, `status=error`, validation/unsupported message | permanent config error | `needs_review`, alert |
| HTTP 500 | handler crash | transient; retry after reconcile |
| Timeout / connection reset | ambiguous | reconcile before retry |
| HTTP 401 | bad/rotated token | do not retry; alert; stop worker |
| HTTP 404 | endpoint missing/old install | permanent; alert |
| Non-JSON body | broken proxy/handler | transient; retry with backoff |

The store must never classify an ambiguous create as “not created”. It must
inspect `list-*` first.

---

## 6. Reconciliation algorithm

Used before any retry of a create, and by maintenance for delivered accounts.

```text
def reconcile_order(order):
    accounts = panel.list_xray() or panel.list_ssh() or panel.list_noobz()
    matches = accounts where username == order.username

    if len(matches) == 0:
        return ABSENT              # safe to create

    if len(matches) > 1:
        # The panel allows the same name on multiple Xray transports.
        # The store uses one transport per order, so more than one match
        # means the name was used outside the store or a prior bug.
        return CONFLICT            # needs_review; do not delete blindly

    match = matches[0]
    if match.transport != order.core:
        return CONFLICT            # same name, different transport

    if order.credentials_stored:
        return ADOPT(match)        # first create succeeded

    return ORPHANED                # this order's name, no credentials yet
```

Recovery for `ORPHANED`:

1. For SSH: the password is known to the store (generated before the call) and
   can be reset with `/password-ssh` if the panel state is uncertain.
2. For Xray: the UUID/links are unrecoverable from the list. Delete the
   account with `/delete-xray`, verify it is gone, then create again with the
   same username. The customer has not received credentials, so this is safe.
3. If delete returns `partial:true` / `failed_from`, stop and set
   `needs_review`. Never create a second account with the same name.
4. Record every step in `order_events` with the panel response (redacted).

The worker sets `reconcile_first=true` after any ambiguous attempt.

---

## 7. Delete semantics

`/delete-xray` deletes the username from **every** transport that holds it and
reports:

```json
{"status":"success","username":"alice","deleted_from":["ws","grpc"]}
```

or, when a transport refuses:

```json
{
  "status":"success",
  "username":"alice",
  "deleted_from":["ws"],
  "partial":true,
  "failed_from":["grpc"]
}
```

A `status=success` with `partial=true` is **not** a full deletion. The store
must check `partial`/`failed_from` before treating the account as gone. This
is especially important before a refund.

`/delete-ssh` answers `{"status":"success","message":"deleted alice"}` and
fails if the user still exists.

---

## 8. Renew semantics

- `/renew-ssh`: body `{username, days}`; returns `previous_expired` and
  `expired`. The API drives `extend-ssh`; it keeps the account's usage.
- `/renew-xray`: body `{username, days, core}`; same shape with `core`.
- If the account is gone (panel `xp` deleted it), the API returns an error.
  The store offers a new-account order instead of retrying.
- The store computes the new expiry from the response's `expired` and its own
  `account_expires_at`; a mismatch is an event and a reconciliation flag.

---

## 9. Serialization and throughput

The API server handles one request at a time. The store therefore:

- runs exactly one worker;
- holds a `flock` on the worker lock file;
- does not call the panel from web request handlers;
- queues admin retries;
- measures queue age, not just request latency.

A create call can take seconds because the panel script restarts a service.
The p95 target (under 2 minutes paid-to-delivered) assumes a small queue and
one panel; if the queue grows, add a panel (Phase 6) rather than parallelize
calls to the same panel.

---

## 10. Idempotency gap and proposed API changes

Current API limitations:

1. No idempotency key on create/renew/delete.
2. No get-one-account endpoint with credentials/links.
3. No renew endpoint for NoobzVPN.
4. No WireGuard/L2TP/SlowDNS endpoints.
5. `list-ssh`/`list-noobz` return text, not structured rows.

The store works within these limits via reconciliation. Recommended
follow-ups in `fn-autosc-api`, in priority order:

| Priority | Change | Why |
| :-- | :-- | :-- |
| P1 | Accept `Idempotency-Key` on create endpoints; store key + response for a window | removes the delete/recreate recovery |
| P1 | `GET /account-xray?username=&core=` returning links/UUID | makes a lost response recoverable without deletion |
| P2 | `GET /account-ssh?username=` structured | same for SSH |
| P2 | Renew NoobzVPN | makes NoobzVPN renewable |
| P3 | WireGuard / L2TP / SlowDNS handlers | expands the catalog |

These are cross-repo changes, not hidden work in the store. If P1 lands, the
store's reconciliation can adopt instead of delete+recreate.
