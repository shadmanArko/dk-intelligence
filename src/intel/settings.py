"""Settings from the environment / `.env`. Secrets never get printed: use `redact()` for logs."""

from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    dk_warehouse_dsn: str | None = None
    dk_warehouse_ssh: str | None = None
    dk_warehouse_ssh_key: Path | None = None
    mip_reader_dsn: str | None = None
    intel_api_url: str | None = None  # tailnet URL of intel-api (ledger export for the live gate)
    dk_inbox_ssh: str | None = None  # upload-only inbox user on the VPS (door 2)
    dk_inbox_ssh_key: Path | None = None
    intel_data_dir: Path = Path("data")
    # --- intel-api (VPS) ---
    intel_db_dsn: str | None = None
    intel_inbox_dir: Path = Path("/inbox")
    intel_store_dir: Path = Path("/store")
    intel_approval_days: int = Field(default=7, ge=1)
    intel_inbox_interval_s: int = Field(default=30, ge=5)
    intel_git_sha: str = "unknown"  # set by deploy/deploy.sh
    intel_public_url: str | None = None  # tailnet URL of the approval page, used in Telegram links
    intel_require_tailscale: bool = True  # web decisions need the Tailscale-User-Login header (tailscale serve)
    intel_warehouse_dsn: str | None = None  # read-only role over the harness docker network (M6)
    harness_predictor_url: str | None = None  # e.g. http://predictor:8000 (the production predictor)
    intel_ledger_start: dt.datetime = dt.datetime(2026, 10, 7, tzinfo=dt.UTC)  # posts before this are not scored
    intel_measure_interval_s: int = Field(default=3600, ge=60)
    # H6 guardrail (ADR 0020): roll a degraded production scorer back by itself ('on'), only report what it would do
    # ('dry_run'), or never ('off').
    intel_auto_rollback: Literal["on", "dry_run", "off"] = "on"
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


def repo_root() -> Path:
    """The checkout root: Claude Code's CLAUDE_PROJECT_DIR when it points at this repo, else from the package location.
    MCP clients start servers from any working directory, so `.env` and `data/` must not be cwd-relative."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and (Path(env) / "src" / "intel").is_dir():
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


class McpSettings(BaseSettings):
    """The few settings the MCP servers need (ADR 0025): no warehouse, inbox or Telegram credentials at all."""

    model_config = SettingsConfigDict(env_file=str(repo_root() / ".env"), env_file_encoding="utf-8", extra="ignore")

    intel_api_url: str | None = None
    intel_data_dir: Path = Path("data")
    mip_dir: Path = Path("../MarketIntelligence Data Platform")
    intel_mcp_audit_dir: Path | None = None  # default: <data>/mcp/audit
    intel_mcp_disabled_tools: str = ""  # kill switch: comma-separated tool names
    intel_pack_dir: Path | None = None  # default: <data>/knowledge
    intel_rag_dsn: str | None = None  # read-only role on the knowledge database (rag_reader)
    intel_rag_embedder: str | None = None  # a key of intel.rag.embed.MODELS; chosen by the P4.2 benchmark
    intel_rag_reranker: str | None = None  # a key of intel.rag.rerank.RERANKERS
    intel_rag_min_evidence: float | None = None  # calibrated on the golden set (ADR 0027)
    intel_rag_min_support: float | None = None

    def path(self, p: Path) -> Path:
        p = p.expanduser()
        return p if p.is_absolute() else repo_root() / p

    @property
    def data_dir(self) -> Path:
        return self.path(self.intel_data_dir)

    @property
    def audit_dir(self) -> Path:
        return self.path(self.intel_mcp_audit_dir) if self.intel_mcp_audit_dir else self.data_dir / "mcp" / "audit"

    @property
    def pack_dir(self) -> Path:
        return self.path(self.intel_pack_dir) if self.intel_pack_dir else self.data_dir / "knowledge"

    @property
    def limits_dir(self) -> Path:
        return self.data_dir / "mcp" / "limits"

    @property
    def disabled_tools(self) -> frozenset[str]:
        return frozenset(t.strip() for t in self.intel_mcp_disabled_tools.split(",") if t.strip())


class MissingSetting(RuntimeError):
    pass


def required[T](value: T | None, env: str) -> T:
    """A setting this code path cannot run without: fail with the variable's name instead of a None error later."""
    if value is None or value == "":
        raise MissingSetting(f"{env} is not set (see .env.example)")
    return value


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
