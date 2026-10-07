"""Settings from the environment / `.env`. Secrets never get printed: use `redact()` for logs."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    dk_warehouse_dsn: str | None = None
    dk_warehouse_ssh: str | None = None
    dk_warehouse_ssh_key: Path | None = None
    mip_reader_dsn: str | None = None
    intel_data_dir: Path = Path("data")
    intel_keep_pulls: int = Field(default=12, ge=1)

    @field_validator("dk_warehouse_ssh_key", "intel_data_dir", mode="after")
    @classmethod
    def _expand(cls, v: Path | None) -> Path | None:
        return v.expanduser() if v else v

    @property
    def warehouse_local_port(self) -> int:
        """The Mac-side tunnel port is whatever port the DSN points at."""
        if not self.dk_warehouse_dsn:
            raise ValueError("DK_WAREHOUSE_DSN is not set")
        return urlsplit(self.dk_warehouse_dsn).port or 5432


def redact(dsn: str | None) -> str:
    """postgresql://user:secret@host:5435/db -> postgresql://user:***@host:5435/db"""
    if not dsn:
        return "<unset>"
    u = urlsplit(dsn)
    if u.password is None:
        return dsn
    return dsn.replace(f":{u.password}@", ":***@", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
