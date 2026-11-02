from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from rohtunnel.db import init_db, migrate, schema_version, table_count, transaction


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "store.sqlite3"
        self.conn, self.applied = init_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def test_initial_migration(self) -> None:
        self.assertEqual(self.applied, ["0001"])
        self.assertEqual(schema_version(self.conn), "0001")
        self.assertGreaterEqual(table_count(self.conn), 10)

    def test_migrate_is_idempotent(self) -> None:
        self.assertEqual(migrate(self.conn), [])

    def test_product_constraints(self) -> None:
        self.conn.execute(
            "INSERT INTO panels (name, base_url, token_file) VALUES (?, ?, ?)",
            ("local", "http://127.0.0.1:9000", "/etc/rohtunnel/panel-token"),
        )
        self.conn.execute(
            """
            INSERT INTO products
                (panel_id, slug, name, protocol, core, duration_days, quota_gb, ip_limit, price_idr)
            VALUES (1, 'vless-ws-30', 'VLESS WS 30d', 'vless', 'ws', 30, 50, 2, 15000)
            """
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO products
                    (panel_id, slug, name, protocol, core,
                     duration_days, quota_gb, ip_limit, price_idr)
                VALUES (1, 'ssh-ws', 'Bad SSH', 'ssh', 'ws', 30, NULL, 1, 10000)
                """
            )

    def test_transaction_rolls_back(self) -> None:
        with self.assertRaises(RuntimeError):
            with transaction(self.conn):
                self.conn.execute("INSERT INTO settings (key, value) VALUES ('x', 'y')")
                raise RuntimeError("boom")
        row = self.conn.execute("SELECT COUNT(*) AS n FROM settings").fetchone()
        self.assertEqual(row["n"], 0)


if __name__ == "__main__":
    unittest.main()
