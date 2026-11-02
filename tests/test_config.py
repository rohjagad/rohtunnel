from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fn_webstore.config import Config, ConfigError


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = Config.from_env()
        self.assertEqual(config.env, "development")
        self.assertEqual(config.bind, "127.0.0.1")
        self.assertEqual(config.port, 8088)
        self.assertEqual(config.panel_timeout, 200.0)
        self.assertEqual(config.db_path, Path("var/webstore.sqlite3"))
        self.assertFalse(config.secret_key)

    def test_overrides_from_env(self) -> None:
        values = {
            "FN_WEBSTORE_ENV": "production",
            "FN_WEBSTORE_BASE_URL": "https://store.example.com",
            "FN_WEBSTORE_BIND": "127.0.0.1",
            "FN_WEBSTORE_PORT": "9001",
            "FN_WEBSTORE_DB": "/tmp/webstore-test.sqlite3",
            "FN_PANEL_BASE_URL": "http://127.0.0.1:9000/",
            "FN_PANEL_TIMEOUT": "180",
            "FN_WEBSTORE_SECRET_KEY": "x" * 32,
        }
        with patch.dict(os.environ, values, clear=True):
            config = Config.from_env()
        self.assertEqual(config.env, "production")
        self.assertEqual(config.base_url, "https://store.example.com")
        self.assertEqual(config.port, 9001)
        self.assertEqual(config.db_path, Path("/tmp/webstore-test.sqlite3"))
        self.assertEqual(config.panel_base_url, "http://127.0.0.1:9000")
        self.assertEqual(config.panel_timeout, 180.0)

    def test_invalid_url(self) -> None:
        with patch.dict(os.environ, {"FN_WEBSTORE_BASE_URL": "store.example.com"}, clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_invalid_port(self) -> None:
        with patch.dict(os.environ, {"FN_WEBSTORE_PORT": "70000"}, clear=True):
            with self.assertRaises(ConfigError):
                Config.from_env()

    def test_production_requires_secret(self) -> None:
        config = Config(env="production", base_url="https://store.example.com")
        with self.assertRaises(ConfigError):
            config.validate_for_serve()

    def test_public_dict_redacts_secret(self) -> None:
        config = Config(secret_key="super-secret-value")
        public = config.public_dict()
        self.assertTrue(public["secret_key_configured"])
        self.assertNotIn("super-secret-value", str(public))


if __name__ == "__main__":
    unittest.main()
