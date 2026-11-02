"""Command-line entry point.

    python3 -m fn_webstore initdb
    python3 -m fn_webstore serve
    python3 -m fn_webstore doctor
    python3 -m fn_webstore version
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import stat
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigError
from .db import connect, init_db, schema_version, table_count
from .server import serve

log = logging.getLogger("fn_webstore")


def _load_config(args: argparse.Namespace) -> Config:
    config = Config.from_env()
    if getattr(args, "db", None):
        config = config.with_overrides(db_path=Path(args.db).expanduser())
    if getattr(args, "bind", None):
        config = config.with_overrides(bind=args.bind)
    if getattr(args, "port", None) is not None:
        config = config.with_overrides(port=args.port)
    return config


def _configure_logging(config: Config) -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def cmd_initdb(args: argparse.Namespace) -> int:
    config = _load_config(args)
    _configure_logging(config)
    conn, applied = init_db(config.db_path)
    try:
        version = schema_version(conn)
    finally:
        conn.close()
    print(f"database: {config.db_path}")
    print(f"schema:   {version or 'none'}")
    if applied:
        print("applied:  " + ", ".join(applied))
    else:
        print("applied:  nothing (already up to date)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    config = _load_config(args)
    _configure_logging(config)
    config.validate_for_serve()
    conn, applied = init_db(config.db_path)
    try:
        version = schema_version(conn)
    finally:
        conn.close()
    log.info(
        "database ready at %s (schema %s, applied %s)",
        config.db_path,
        version,
        applied or "none",
    )
    serve(config)
    return 0


def _file_mode_ok(path: Path) -> bool:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    return mode & 0o077 == 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _load_config(args)
    _configure_logging(config)
    print(json.dumps(config.public_dict(), indent=2, sort_keys=True))
    print()

    failures = 0
    warnings = 0

    if not config.db_path.exists():
        print(f"FAIL database: not found at {config.db_path} (run initdb)")
        failures += 1
    else:
        try:
            conn = connect(config.db_path, create_parent=False)
            try:
                version = schema_version(conn)
                tables = table_count(conn)
                if version:
                    print(f"OK   database: schema {version}, {tables} tables")
                else:
                    print("FAIL database: schema_migrations is empty (run initdb)")
                    failures += 1
            finally:
                conn.close()
        except (OSError, sqlite3.Error) as exc:
            print(f"FAIL database: {exc}")
            failures += 1

    if not config.panel_token_file.exists():
        message = f"panel token file not found at {config.panel_token_file}"
        if config.env == "production":
            print(f"FAIL panel token: {message}")
            failures += 1
        else:
            print(f"WARN panel token: {message} (required before provisioning)")
            warnings += 1
    elif config.panel_token_file.stat().st_size == 0:
        print(f"FAIL panel token: {config.panel_token_file} is empty")
        failures += 1
    else:
        print(f"OK   panel token: {config.panel_token_file}")
        if not _file_mode_ok(config.panel_token_file):
            print("WARN panel token: file mode is not 0600")
            warnings += 1

    if config.env == "production" and not config.secret_key:
        print("FAIL secret: FN_WEBSTORE_SECRET_KEY is required in production")
        failures += 1
    elif not config.secret_key:
        print("WARN secret: FN_WEBSTORE_SECRET_KEY is not set (development only)")
        warnings += 1
    else:
        print("OK   secret: configured (value not printed)")

    insecure_base = (
        config.env == "production"
        and config.base_url.startswith("http://")
        and "127.0.0.1" not in config.base_url
    )
    if insecure_base:
        print("FAIL base URL: production must use https://")
        failures += 1

    print()
    print(f"result: {failures} failure(s), {warnings} warning(s)")
    return 1 if failures else 0


def cmd_version(args: argparse.Namespace) -> int:
    print(f"fn-webstore {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fn-webstore",
        description="VPN storefront and order automation for FN AutoSC",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    initdb = sub.add_parser("initdb", help="create or migrate the SQLite database")
    initdb.add_argument("--db", help="override FN_WEBSTORE_DB")
    initdb.set_defaults(func=cmd_initdb)

    migrate = sub.add_parser("migrate", help="alias for initdb (forward-only migrations)")
    migrate.add_argument("--db", help="override FN_WEBSTORE_DB")
    migrate.set_defaults(func=cmd_initdb)

    serve_cmd = sub.add_parser("serve", help="run the HTTP service")
    serve_cmd.add_argument("--db", help="override FN_WEBSTORE_DB")
    serve_cmd.add_argument("--bind", help="override FN_WEBSTORE_BIND")
    serve_cmd.add_argument("--port", type=int, help="override FN_WEBSTORE_PORT")
    serve_cmd.set_defaults(func=cmd_serve)

    doctor = sub.add_parser("doctor", help="check configuration and database health")
    doctor.add_argument("--db", help="override FN_WEBSTORE_DB")
    doctor.set_defaults(func=cmd_doctor)

    version = sub.add_parser("version", help="print the version")
    version.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
