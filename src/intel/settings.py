"""Settings from the environment / `.env`. Secrets never get printed: use `redact()` for logs."""

from __future__ import annotations

import datetime as dt
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
    dk_inbox_ssh: str | None = None  # upload-only inbox user on the VPS (door 2)
    dk_inbox_ssh_key: Path | None = None
    intel_data_dir: Path = Path("data")
    # --- intel-api (VPS) ---
    intel_db_dsn: str | None = None
    intel_inbox_dir: Path = Path("/inbox")
    intel_store_dir: Path = Path("/store")
    intel_approval_days: int = Field(default=7, ge=1)
    intel_inbox_interval_s: int = Field(default=30, ge=5)
    intel_public_url: str | None = None  # tailnet URL of the approval page, used in Telegram links
    intel_require_tailscale: bool = True  # web decisions need the Tailscale-User-Login header (tailscale serve)
    intel_warehouse_dsn: str | None = None  # read-only role over the harness docker network (M6)
    harness_predictor_url: str | None = None  # e.g. http://predictor:8000 (the production predictor)
    intel_ledger_start: dt.datetime = dt.datetime(2026, 10, 7, tzinfo=dt.UTC)  # posts before this are not scored
    intel_measure_interval_s: int = Field(default=3600, ge=60)
    telegram_bot_token: str | None = None
    telegram_chat_id: int | None = None
    telegram_polling: bool = True
    harness_dir: Path = Path("../dhaka_kacchi_ai_harness")
    mip_dir: Path = Path("../MarketIntelligence Data Platform")
    intel_keep_pulls: int = Field(default=12, ge=1)

    @field_validator(
        "dk_warehouse_ssh_key", "dk_inbox_ssh_key", "intel_data_dir", "harness_dir", "mip_dir", mode="after"
    )
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
