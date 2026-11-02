# Deployment examples

These are templates, not an automatic installer. The service currently ships
`serve` only; the worker and maintenance commands arrive in Phase 2/5. The
worker/maintenance unit files are included as target examples and are marked
accordingly.

## 1. Service user and directories

```bash
sudo useradd --system --home /var/lib/fn-webstore \
     --shell /usr/sbin/nologin fnwebstore

sudo install -d -o root:root -m 0755 /opt/fn-webstore
sudo install -d -o fnwebstore:fnwebstore -m 0700 \
     /var/lib/fn-webstore /var/backups/fn-webstore

sudo install -o root:root -m 0644 \
     deploy/fn-webstore.service.example \
     /etc/systemd/system/fn-webstore.service
```

Copy the repository to `/opt/fn-webstore` (or a release directory) and make
it read-only to the service user.

## 2. Configuration

```bash
sudo install -d -o root:root -m 0755 /etc/fn-webstore
sudo install -o fnwebstore:fnwebstore -m 0600 /dev/null /etc/fn-webstore/env
sudo editor /etc/fn-webstore/env
```

Start from `.env.example`. At minimum set:

```ini
FN_WEBSTORE_ENV=production
FN_WEBSTORE_BASE_URL=https://store.example.com
FN_WEBSTORE_DB=/var/lib/fn-webstore/webstore.sqlite3
FN_WEBSTORE_SECRET_KEY=<48 random characters>
FN_PANEL_BASE_URL=http://127.0.0.1:9000
FN_PANEL_TOKEN_FILE=/etc/fn-webstore/panel-token
```

## 3. Panel token

Use a **dedicated** token for the store. Append one line to `/etc/xray/.key`
(the API accepts any line in that file), then copy only that line to the
store's own file:

```bash
TOKEN=$(head -c 32 /dev/urandom | base64 | tr -d '/+=\n' | head -c 40)
echo "$TOKEN" | sudo tee -a /etc/xray/.key >/dev/null
echo "$TOKEN" | sudo tee /etc/fn-webstore/panel-token >/dev/null
sudo chown fnwebstore:fnwebstore /etc/fn-webstore/panel-token
sudo chmod 0600 /etc/fn-webstore/panel-token
sudo systemctl restart api.service
```

Do not give `www-data` or the webroot access to `/etc/xray/.key`.

## 4. Database

```bash
sudo -u fnwebstore sh -c \
  'set -a; . /etc/fn-webstore/env; set +a; exec python3 -m fn_webstore initdb'
sudo -u fnwebstore sh -c \
  'set -a; . /etc/fn-webstore/env; set +a; exec python3 -m fn_webstore doctor'
```

The env file is sourced as shell, so keep values shell-safe (quote values
with spaces). Do not put untrusted content in it.

## 5. systemd

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fn-webstore.service
sudo systemctl status fn-webstore.service
```

## 6. nginx

Install [`nginx.conf.example`](nginx.conf.example) as a site, adjust the
domain and certificate paths, then:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 7. Backups

```bash
sudo -u fnwebstore sqlite3 /var/lib/fn-webstore/webstore.sqlite3 \
  ".backup '/var/backups/fn-webstore/db-$(date +%F).sqlite3'"
```

Encrypt before copying off-host. Test a restore monthly; a backup that has
never been restored is not a backup.
