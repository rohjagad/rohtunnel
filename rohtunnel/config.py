"""Environment-based configuration.

Secrets are read from environment variables or from files. The panel token is
never stored in the database, and config reprs never print secret values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """Raised when the environment configuration is invalid."""


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        if default is None:
            raise ConfigError(f"missing required environment variable: {name}")
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _path(name: str, default: str) -> Path:
    raw = _env(name, default)
    return Path(raw).expanduser()


def _validate_url(name: str, value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConfigError(f"{name} must be an http(s) URL, got {value!r}")
    return value.rstrip("/")


@dataclass(frozen=True)
class Config:
    env: str = "development"
    base_url: str = "http://127.0.0.1:8088"
    bind: str = "127.0.0.1"
    port: int = 8088
    db_path: Path = Path("var/rohtunnel.sqlite3")
    log_level: str = "INFO"
    timezone: str = "Asia/Jakarta"

    panel_base_url: str = "http://127.0.0.1:9000"
    panel_token_file: Path = Path("var/panel-token")
    panel_timeout: float = 200.0

    payment_provider: str = "manual"
    secret_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Config":
        env = _env("ROHTUNNEL_ENV", "development")
        port = _env_int("ROHTUNNEL_PORT", 8088)
        if not 0 <= port <= 65535:
            raise ConfigError("ROHTUNNEL_PORT must be between 0 and 65535")

        timeout = _env_float("ROHTUNNEL_PANEL_TIMEOUT", 200.0)
        if timeout <= 0:
            raise ConfigError("ROHTUNNEL_PANEL_TIMEOUT must be greater than zero")

        config = cls(
            env=env,
            base_url=_validate_url(
                "ROHTUNNEL_BASE_URL", _env("ROHTUNNEL_BASE_URL", "http://127.0.0.1:8088")
            ),
            bind=_env("ROHTUNNEL_BIND", "127.0.0.1"),
            port=port,
            db_path=_path("ROHTUNNEL_DB", "var/rohtunnel.sqlite3"),
            log_level=_env("ROHTUNNEL_LOG_LEVEL", "INFO").upper(),
            timezone=_env("ROHTUNNEL_TIMEZONE", "Asia/Jakarta"),
            panel_base_url=_validate_url(
                "ROHTUNNEL_PANEL_BASE_URL",
                _env("ROHTUNNEL_PANEL_BASE_URL", "http://127.0.0.1:9000"),
            ),
            panel_token_file=_path("ROHTUNNEL_PANEL_TOKEN_FILE", "var/panel-token"),
            panel_timeout=timeout,
            payment_provider=_env("ROHTUNNEL_PAYMENT_PROVIDER", "manual"),
            secret_key=_env("ROHTUNNEL_SECRET_KEY", ""),
        )
        if config.log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ConfigError("ROHTUNNEL_LOG_LEVEL is not a valid Python log level")
        return config

    def with_overrides(self, **kwargs: object) -> "Config":
        """Return a copy with explicit command-line overrides."""
        return replace(self, **kwargs)

    def public_dict(self) -> dict[str, object]:
        """A doctor/status view with no secret values."""
        return {
            "env": self.env,
            "base_url": self.base_url,
            "bind": f"{self.bind}:{self.port}",
            "db_path": str(self.db_path),
            "log_level": self.log_level,
            "timezone": self.timezone,
            "panel_base_url": self.panel_base_url,
            "panel_token_file": str(self.panel_token_file),
            "panel_timeout": self.panel_timeout,
            "payment_provider": self.payment_provider,
            "secret_key_configured": bool(self.secret_key),
        }

    def validate_for_serve(self) -> None:
        """Fail closed on obviously unsafe production configuration."""
        if self.env == "production":
            if not self.secret_key:
                raise ConfigError(
                    "ROHTUNNEL_SECRET_KEY is required when ROHTUNNEL_ENV=production"
                )
            if self.base_url.startswith("http://") and "127.0.0.1" not in self.base_url:
                raise ConfigError(
                    "ROHTUNNEL_BASE_URL must be https:// in production (except localhost)"
                )
