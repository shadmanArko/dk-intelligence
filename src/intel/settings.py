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
    # HTTP (ADR 0029): the signing secret (written by `intel mcp init --write-env`), two loopback listeners (local
    # clients; the only target of `tailscale serve`), Host allowlist, owner logins trusted on the tailnet listener only
    intel_mcp_secret: str | None = None
    intel_mcp_host: str = "mac"  # the token audience is dk_intel@<host>
    intel_mcp_http_port: int = 8766
    intel_mcp_tailnet_port: int = 8767
    intel_mcp_allowed_hosts: str = ""  # extra Host names, e.g. this Mac's tailnet name (127.0.0.1, localhost always)
    intel_mcp_owner_logins: str = ""  # comma-separated Tailscale logins allowed to use the owner profile remotely
    intel_mcp_revoked_file: Path | None = None  # default: <data>/mcp/revoked.txt
    intel_pack_dir: Path | None = None  # default: <data>/knowledge
    intel_rag_dsn: str | None = None  # read-only role on the knowledge database (rag_reader)
    intel_rag_admin_dsn: str | None = None  # the builder role: loads packs (`intel pack load`), never the server
    # Chosen by the P4.2 benchmark (ADR 0027, docs/results/P4_2_rag_reviews.md): the best stack that runs on a CPU
    intel_rag_embedder: str | None = "onnx:harrier-0.6b"  # int8 ONNX, ~140 ms per query on CPU
    intel_rag_reranker: str | None = "onnx:bge-reranker-v2-m3"  # int8 ONNX cross-encoder, ~1 s for 20 pairs
    intel_rag_mode: str = "dense"  # dense beat hybrid for every model on dev; hybrid stays available
    intel_rag_rerank_pool: int = 20
    # Evidence levels by the top rerank score (dev calibration, ADR 0027): >= strong -> "strong"; between -> "weak"
    # (hits returned and flagged: broad questions score low against any single review); < min -> "none".
    intel_rag_min_evidence: float | None = 0.003
    intel_rag_strong_evidence: float = 0.5
    intel_rag_min_support: float | None = 0.5  # a claim needs this rerank score from a cited source

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
    def revoked_file(self) -> Path:
        return (
            self.path(self.intel_mcp_revoked_file)
            if self.intel_mcp_revoked_file
            else self.data_dir / "mcp" / "revoked.txt"
        )

    @property
    def allowed_hosts(self) -> list[str]:
        extra = [h.strip() for h in self.intel_mcp_allowed_hosts.split(",") if h.strip()]
        return ["127.0.0.1", "localhost", *extra]

    @property
    def owner_logins(self) -> frozenset[str]:
        return frozenset(x.strip().lower() for x in self.intel_mcp_owner_logins.split(",") if x.strip())

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
