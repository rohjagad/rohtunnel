"""Minimal stdlib HTTP service.

Phase 0 only exposes health/readiness and a placeholder page. Storefront,
order, admin and webhook routes are added in later phases; the process shape
(bind, logging, headers, DB checks) is established here.
"""

from __future__ import annotations

import html
import json
import logging
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import __version__
from .config import Config
from .db import connect, schema_version

log = logging.getLogger("rohtunnel.http")


class App:
    def __init__(self, config: Config):
        self.config = config

    def database(self) -> sqlite3.Connection:
        return connect(self.config.db_path)

    def readiness(self) -> tuple[bool, dict[str, object]]:
        checks: dict[str, object] = {}
        ok = True

        try:
            conn = self.database()
            try:
                conn.execute("SELECT 1").fetchone()
                checks["database"] = "ok"
                checks["schema_version"] = schema_version(conn)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - report, do not crash readiness
            ok = False
            checks["database"] = f"error: {exc}"

        if self.config.panel_token_file.is_file():
            checks["panel_token_file"] = "ok"
        else:
            ok = False
            checks["panel_token_file"] = f"missing: {self.config.panel_token_file}"

        return ok, checks


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _home_page(config: Config) -> bytes:
    version = html.escape(__version__)
    env = html.escape(config.env)
    base_url = html.escape(config.base_url)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RohTunnel</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ margin: 0; font-family: ui-sans-serif, system-ui, sans-serif;
            background: #0b1020; color: #e6e9f2; }}
    main {{ max-width: 720px; margin: 12vh auto; padding: 0 24px; }}
    h1 {{ font-size: 2.4rem; margin: 0 0 .4rem; letter-spacing: -.03em; }}
    .tag {{ display: inline-block; padding: .2rem .6rem; border-radius: 999px;
             background: #1b2747; color: #9db4ff; font-size: .8rem; }}
    p {{ color: #aab3c8; line-height: 1.6; }}
    code {{ background: #141b31; padding: .15rem .4rem; border-radius: 4px; }}
    .links a {{ color: #8fb0ff; margin-right: 1rem; }}
    footer {{ margin-top: 3rem; color: #66708a; font-size: .85rem; }}
  </style>
</head>
<body>
<main>
  <span class="tag">Phase 0 &middot; walking skeleton</span>
  <h1>RohTunnel</h1>
  <p>VPN storefront and order automation for FN AutoSC. The service is alive;
     the storefront, checkout, payment and provisioning routes arrive in the
     next phases.</p>
  <p><strong>Environment:</strong> {env}<br>
     <strong>Public URL:</strong> {base_url}<br>
     <strong>Version:</strong> {version}</p>
  <p class="links">
    <a href="/healthz">/healthz</a>
    <a href="/readyz">/readyz</a>
  </p>
  <footer>Panel API is never called from this page or from a browser.</footer>
</main>
</body>
</html>
""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = f"rohtunnel/{__version__}"
    protocol_version = "HTTP/1.1"
    timeout = 30

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "text/plain; charset=utf-8",
        *,
        head: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if content_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _dispatch(self, *, head: bool = False) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send(
                200,
                _json_bytes({"status": "ok", "version": __version__}),
                "application/json; charset=utf-8",
                head=head,
            )
            return
        if path == "/readyz":
            ok, checks = self.app.readiness()
            self._send(
                200 if ok else 503,
                _json_bytes({"status": "ready" if ok else "not_ready", "checks": checks}),
                "application/json; charset=utf-8",
                head=head,
            )
            return
        if path == "/":
            self._send(200, _home_page(self.app.config), "text/html; charset=utf-8", head=head)
            return
        if path == "/favicon.ico":
            self._send(204, b"", head=head)
            return
        self._send(404, b"Not found\n", "text/plain; charset=utf-8", head=head)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        self._dispatch(head=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: App):
        super().__init__(address, Handler)
        self.app = app


def create_server(config: Config) -> Server:
    return Server((config.bind, config.port), App(config))


def serve(config: Config) -> None:
    config.validate_for_serve()
    server = create_server(config)
    host, port = server.server_address[:2]
    log.info("RohTunnel %s listening on %s:%s", __version__, host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutdown requested")
    finally:
        server.server_close()
