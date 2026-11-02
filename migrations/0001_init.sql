-- RohTunnel initial schema
-- Migration 0001. Applied by `rohtunnel initdb` / `rohtunnel migrate`.
-- All timestamps are UTC ISO-8601 text. Money is integer IDR.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS panels (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,
    base_url        TEXT    NOT NULL,
    token_file      TEXT    NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY,
    panel_id        INTEGER NOT NULL REFERENCES panels(id) ON DELETE RESTRICT,
    slug            TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    protocol        TEXT    NOT NULL CHECK (protocol IN ('ssh', 'vmess', 'vless', 'trojan', 'noobz')),
    core            TEXT    CHECK (core IS NULL OR core IN ('ws', 'http', 'xhttp', 'grpc')),
    duration_days   INTEGER NOT NULL CHECK (duration_days > 0),
    quota_gb        INTEGER CHECK (quota_gb IS NULL OR quota_gb > 0),
    ip_limit        INTEGER NOT NULL DEFAULT 1 CHECK (ip_limit > 0),
    price_idr       INTEGER NOT NULL CHECK (price_idr >= 0),
    currency        TEXT    NOT NULL DEFAULT 'IDR',
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (
        (protocol IN ('ssh', 'noobz') AND core IS NULL)
        OR (protocol IN ('vmess', 'vless', 'trojan') AND core IN ('ws', 'http', 'xhttp', 'grpc'))
    ),
    CHECK (
        (protocol IN ('vmess', 'vless', 'trojan') AND quota_gb IS NOT NULL)
        OR (protocol IN ('ssh', 'noobz') AND quota_gb IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS products_active_idx
    ON products (active, sort_order, id);

CREATE TABLE IF NOT EXISTS admin_users (
    id              INTEGER PRIMARY KEY,
    username        TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,
    role            TEXT    NOT NULL DEFAULT 'admin' CHECK (role IN ('owner', 'admin', 'support')),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_login_at   TEXT
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    id              INTEGER PRIMARY KEY,
    admin_id        INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash      TEXT    NOT NULL UNIQUE,
    csrf_token      TEXT    NOT NULL,
    expires_at      TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen_at    TEXT,
    ip              TEXT,
    user_agent      TEXT
);

CREATE INDEX IF NOT EXISTS admin_sessions_expiry_idx
    ON admin_sessions (expires_at);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- The store's mirror of a panel account. `origin_order_id` is the order that
-- first created it; renewals update this row and point their own order at it
-- through `orders.account_id`.
CREATE TABLE IF NOT EXISTS accounts (
    id                  INTEGER PRIMARY KEY,
    panel_id            INTEGER NOT NULL REFERENCES panels(id) ON DELETE RESTRICT,
    origin_order_id     INTEGER,
    protocol            TEXT    NOT NULL CHECK (protocol IN ('ssh', 'vmess', 'vless', 'trojan', 'noobz')),
    core                TEXT    CHECK (core IS NULL OR core IN ('ws', 'http', 'xhttp', 'grpc')),
    username            TEXT    NOT NULL,
    credentials_json    TEXT    NOT NULL,
    panel_expiry_raw    TEXT,
    expires_at          TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'expired', 'suspended', 'deleted', 'unknown')),
    last_seen_at        TEXT,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (
        (protocol IN ('ssh', 'noobz') AND core IS NULL)
        OR (protocol IN ('vmess', 'vless', 'trojan') AND core IN ('ws', 'http', 'xhttp', 'grpc'))
    )
);

-- A username is unique per panel while the account is alive. Deleted rows are
-- kept for history and do not block reusing the name later.
CREATE UNIQUE INDEX IF NOT EXISTS accounts_panel_username_active_idx
    ON accounts (panel_id, username)
    WHERE status != 'deleted';

CREATE INDEX IF NOT EXISTS accounts_expiry_idx
    ON accounts (expires_at, status);

CREATE TABLE IF NOT EXISTS orders (
    id                      INTEGER PRIMARY KEY,
    public_id               TEXT    NOT NULL UNIQUE,
    access_token_hash       TEXT,
    idempotency_key         TEXT    UNIQUE,
    panel_id                INTEGER NOT NULL REFERENCES panels(id) ON DELETE RESTRICT,
    product_id              INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_snapshot        TEXT    NOT NULL,
    account_id              INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    renewal_of_order_id     INTEGER REFERENCES orders(id) ON DELETE SET NULL,

    customer_name           TEXT    NOT NULL,
    customer_email          TEXT    NOT NULL,
    customer_phone          TEXT,
    customer_telegram       TEXT,

    requested_username      TEXT,
    username                TEXT,

    amount_idr              INTEGER NOT NULL CHECK (amount_idr >= 0),
    currency                TEXT    NOT NULL DEFAULT 'IDR',

    payment_provider        TEXT    NOT NULL DEFAULT 'manual',
    payment_ref             TEXT,
    payment_instructions    TEXT,
    payment_expires_at      TEXT,
    payment_status          TEXT    NOT NULL DEFAULT 'unpaid'
                                    CHECK (payment_status IN ('unpaid', 'paid', 'expired', 'refunded', 'failed')),
    paid_at                 TEXT,

    fulfillment_status      TEXT    NOT NULL DEFAULT 'pending'
                                    CHECK (fulfillment_status IN ('pending', 'queued', 'provisioning',
                                                                  'retry_wait', 'delivered', 'needs_review',
                                                                  'cancelled')),
    fulfillment_attempts    INTEGER NOT NULL DEFAULT 0 CHECK (fulfillment_attempts >= 0),
    last_error              TEXT,
    next_attempt_at         TEXT,

    account_expires_at      TEXT,
    terms_version           TEXT    NOT NULL DEFAULT 'v1',
    notes                   TEXT,

    created_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at            TEXT,
    cancelled_at            TEXT
);

CREATE INDEX IF NOT EXISTS orders_payment_fulfillment_idx
    ON orders (payment_status, fulfillment_status);

CREATE INDEX IF NOT EXISTS orders_customer_email_idx
    ON orders (customer_email, created_at);

CREATE INDEX IF NOT EXISTS orders_created_idx
    ON orders (created_at);

CREATE INDEX IF NOT EXISTS orders_retry_idx
    ON orders (fulfillment_status, next_attempt_at);

CREATE TABLE IF NOT EXISTS payment_events (
    id              INTEGER PRIMARY KEY,
    provider        TEXT    NOT NULL,
    event_id        TEXT    NOT NULL,
    order_id        INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    provider_ref    TEXT,
    status          TEXT    NOT NULL,
    amount_idr      INTEGER,
    currency        TEXT,
    signature_ok    INTEGER NOT NULL DEFAULT 0 CHECK (signature_ok IN (0, 1)),
    payload_json    TEXT    NOT NULL,
    processed_at    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (provider, event_id)
);

CREATE INDEX IF NOT EXISTS payment_events_order_idx
    ON payment_events (order_id, created_at);

CREATE TABLE IF NOT EXISTS order_events (
    id              INTEGER PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    actor           TEXT    NOT NULL,
    type            TEXT    NOT NULL,
    message         TEXT    NOT NULL DEFAULT '',
    data_json       TEXT    NOT NULL DEFAULT '{}',
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS order_events_order_idx
    ON order_events (order_id, created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY,
    kind            TEXT    NOT NULL CHECK (kind IN ('provision', 'notify', 'reconcile',
                                                     'expire', 'remind', 'payment_sync')),
    order_id        INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    dedupe_key      TEXT,
    status          TEXT    NOT NULL DEFAULT 'queued'
                            CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts    INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    run_after       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    locked_at       TEXT,
    last_error      TEXT,
    payload_json    TEXT    NOT NULL DEFAULT '{}',
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (kind, dedupe_key)
);

CREATE INDEX IF NOT EXISTS jobs_claim_idx
    ON jobs (status, run_after, id);

CREATE INDEX IF NOT EXISTS jobs_order_idx
    ON jobs (order_id, status);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version         TEXT    PRIMARY KEY,
    applied_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
