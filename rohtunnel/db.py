"""SQLite connections and forward-only migrations."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_RE = re.compile(r"^(?P<version>\d+)_[A-Za-z0-9_.-]+\.sql$")


def connect(path: str | Path, *, create_parent: bool = True) -> sqlite3.Connection:
    """Open a connection with the settings every store process expects."""
    path = Path(path)
    if str(path) != ":memory:" and create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    return {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}


def _migration_files(migrations_dir: Path) -> list[tuple[str, Path]]:
    if not migrations_dir.is_dir():
        raise FileNotFoundError(f"migrations directory not found: {migrations_dir}")

    files: list[tuple[str, Path]] = []
    for path in sorted(migrations_dir.iterdir()):
        match = MIGRATION_RE.match(path.name)
        if match:
            files.append((match.group("version"), path))
    if not files:
        raise FileNotFoundError(f"no migrations found in {migrations_dir}")
    return files


def migrate(
    conn: sqlite3.Connection, migrations_dir: str | Path = DEFAULT_MIGRATIONS_DIR
) -> list[str]:
    """Apply pending migrations and return the versions applied.

    Each migration runs inside a transaction together with its bookkeeping row.
    A failure rolls the whole migration back, so a version is either fully
    applied or not recorded at all.
    """
    migrations_dir = Path(migrations_dir)
    applied = _applied_versions(conn)
    newly_applied: list[str] = []

    for version, path in _migration_files(migrations_dir):
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        escaped = version.replace("'", "''")
        try:
            # The bookkeeping row is committed with the migration itself, so
            # a version is never recorded without its schema (or vice versa).
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{sql}\n"
                f"INSERT INTO schema_migrations (version) VALUES ('{escaped}');\n"
                "COMMIT;"
            )
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        newly_applied.append(version)
        applied.add(version)

    return newly_applied


def init_db(
    path: str | Path, migrations_dir: str | Path = DEFAULT_MIGRATIONS_DIR
) -> tuple[sqlite3.Connection, list[str]]:
    conn = connect(path)
    try:
        applied = migrate(conn, migrations_dir)
    except Exception:
        conn.close()
        raise
    return conn, applied


def schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return row["version"] if row else None


def table_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return int(row["n"])


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Short write transaction with the store's claim semantics."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
