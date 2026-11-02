from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from fn_webstore.config import Config
from fn_webstore.db import init_db
from fn_webstore.server import create_server


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.db_path = root / "store.sqlite3"
        init_db(self.db_path)[0].close()
        self.token_file = root / "panel-token"
        self.token_file.write_text("test-token\n", encoding="utf-8")
        os.chmod(self.token_file, 0o600)

        self.config = Config(
            env="development",
            base_url="http://127.0.0.1:0",
            bind="127.0.0.1",
            port=0,
            db_path=self.db_path,
            panel_token_file=self.token_file,
        )
        self.server = create_server(self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tempdir.cleanup()

    def _get(self, path: str) -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, exc.read(), exc.headers.get("Content-Type", "")
            finally:
                exc.close()

    def test_healthz(self) -> None:
        status, body, content_type = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("application/json"))
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ok")

    def test_readyz(self) -> None:
        status, body, _ = self._get("/readyz")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["checks"]["database"], "ok")

    def test_home(self) -> None:
        status, body, content_type = self._get("/")
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/html"))
        self.assertIn(b"fn-webstore", body)

    def test_not_found(self) -> None:
        status, _, _ = self._get("/nope")
        self.assertEqual(status, 404)


class ReadinessTests(unittest.TestCase):
    def test_missing_token_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "store.sqlite3"
            init_db(db_path)[0].close()
            config = Config(
                env="development",
                db_path=db_path,
                panel_token_file=root / "missing-token",
            )
            server = create_server(config)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                try:
                    with urllib.request.urlopen(f"http://{host}:{port}/readyz", timeout=5):
                        status = 200
                except urllib.error.HTTPError as exc:
                    try:
                        status = exc.code
                    finally:
                        exc.close()
                self.assertEqual(status, 503)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
