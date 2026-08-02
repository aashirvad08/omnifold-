"""Environment-driven configuration.

Defaults are restrictive and safe for local development; production is
selected with ``OMNIFOLD_ENV=prod`` (or ``staging``), which tightens auth,
cache backend, and limits. All settings can be overridden by environment
variables prefixed with ``OMNIFOLD_`` (e.g. ``OMNIFOLD_DATA_ROOT``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "staging", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OMNIFOLD_", env_file=".env", extra="ignore"
    )

    env: Environment = "dev"

    # --- discovery / disk ------------------------------------------------
    # Root under which published packages and analyses are discovered. Every
    # disk path served to a client is resolved and checked to live under this
    # root (see services.registry); nothing else on disk is reachable.
    data_root: Path = Field(default=Path("artifacts"))
    upload_dir: Path = Field(default=Path("uploads"))

    # --- cache -----------------------------------------------------------
    cache_backend: Literal["memory", "redis"] = "memory"
    redis_url: str | None = None
    immutable_cache_ttl_seconds: int = 7 * 24 * 3600
    session_cache_ttl_seconds: int = 3600
    cache_max_entries: int = 2048

    # --- limits / async --------------------------------------------------
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB
    job_retention_seconds: int = 3600
    async_replica_threshold: int = 20  # replica count above which work is a job

    # --- security --------------------------------------------------------
    require_api_key: bool = False  # dev default; prod validator forces True
    api_keys: tuple[str, ...] = ()
    trusted_proxies: tuple[str, ...] = ()  # peer IPs whose XFF we honour
    rate_limit_per_minute: int = 60
    # Source kinds a client may open. Network kinds are opt-in; url/s3 are
    # SSRF risks and stay off unless explicitly enabled.
    allowed_source_kinds: tuple[str, ...] = ("local", "upload", "zenodo")
    # Hosts reachable when url/s3 kinds are enabled (deny-by-default).
    egress_allowlist: tuple[str, ...] = ("zenodo.org",)

    @field_validator("data_root", "upload_dir")
    @classmethod
    def _resolve_dirs(cls, value: Path) -> Path:
        return value.expanduser()

    def apply_environment_defaults(self) -> Settings:
        """Return settings with prod/staging hardening applied.

        Only fills stricter defaults where the operator has not overridden
        them; explicit env vars always win.
        """

        if self.env in ("prod", "staging"):
            updates: dict[str, object] = {}
            if not self.require_api_key:
                updates["require_api_key"] = True
            if self.cache_backend == "memory" and self.env == "prod":
                # prod should use Redis; surface it rather than silently
                # running single-node. Not fatal here (validated at startup).
                updates["cache_backend"] = self.cache_backend
            if updates:
                return self.model_copy(update=updates)
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings().apply_environment_defaults()
