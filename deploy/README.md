# Deployment examples

These are templates, not an automatic installer. The service currently ships
`serve` only; the worker and maintenance commands arrive in Phase 2/5. The
worker/maintenance unit files are included as target examples and are marked
accordingly.

## 1. Service user and directories

```bash
sudo useradd --system --home /var/lib/rohtunnel \
     --shell /usr/sbin/nologin rohtunnel

sudo install -d -o root:root -m 0755 /opt/rohtunnel
sudo install -d -o rohtunnel:rohtunnel -m 0700 \
     /var/lib/rohtunnel /var/backups/rohtunnel

sudo install -o root:root -m 0644 \
     deploy/rohtunnel.service.example \
     /etc/systemd/system/rohtunnel.service
```

Copy the repository to `/opt/rohtunnel` (or a release directory) and make
it read-only to the service user.

## 2. Configuration

```bash
sudo install -d -o root:root -m 0755 /etc/rohtunnel
sudo install -o rohtunnel:rohtunnel -m 0600 /dev/null /etc/rohtunnel/env
sudo editor /etc/rohtunnel/env
```

Start from `.env.example`. At minimum set:

```ini
ROHTUNNEL_ENV=production
ROHTUNNEL_BASE_URL=https://store.example.com
ROHTUNNEL_DB=/var/lib/rohtunnel/rohtunnel.sqlite3
ROHTUNNEL_SECRET_KEY=<48 random characters>
ROHTUNNEL_PANEL_BASE_URL=http://127.0.0.1:9000
ROHTUNNEL_PANEL_TOKEN_FILE=/etc/rohtunnel/panel-token
```

## 3. Panel token

Use a **dedicated** token for the store. Append one line to `/etc/xray/.key`
(the API accepts any line in that file), then copy only that line to the
store's own file:

```bash
TOKEN=$(head -c 32 /dev/urandom | base64 | tr -d '/+=\n' | head -c 40)
echo "$TOKEN" | sudo tee -a /etc/xray/.key >/dev/null
echo "$TOKEN" | sudo tee /etc/rohtunnel/panel-token >/dev/null
sudo chown rohtunnel:rohtunnel /etc/rohtunnel/panel-token
sudo chmod 0600 /etc/rohtunnel/panel-token
sudo systemctl restart api.service
```

Do not give `www-data` or the webroot access to `/etc/xray/.key`.

## 4. Database

```bash
sudo -u rohtunnel sh -c \
  'set -a; . /etc/rohtunnel/env; set +a; exec python3 -m rohtunnel initdb'
sudo -u rohtunnel sh -c \
  'set -a; . /etc/rohtunnel/env; set +a; exec python3 -m rohtunnel doctor'
```

The env file is sourced as shell, so keep values shell-safe (quote values
with spaces). Do not put untrusted content in it.

## 5. systemd

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rohtunnel.service
sudo systemctl status rohtunnel.service
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
sudo -u rohtunnel sqlite3 /var/lib/rohtunnel/rohtunnel.sqlite3 \
  ".backup '/var/backups/rohtunnel/db-$(date +%F).sqlite3'"
```

Encrypt before copying off-host. Test a restore monthly; a backup that has
never been restored is not a backup.
