"""The Sunday run on the homelab server (ADR 0044): what it needs, checked before it starts.

The Mac's `scripts/preflight.sh` checks Tailscale and two ssh tunnels. On the server there are no tunnels to the
databases (the container joins their Docker networks), so this checks the real things instead: the data platform's
status file, a login to `mip` and to the knowledge database, intel-api over the tailnet, and the two door keys. It never
prints a secret: DSNs only through `redact`, errors only by type.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import psycopg

from intel.settings import McpSettings, Settings, redact


def _db(dsn: str | None, name: str, want_db: str) -> str | None:
    if not dsn:
        return f"{name}: DSN not set"
    try:
        with psycopg.connect(dsn, connect_timeout=10) as c:
            row = c.execute("select current_database()").fetchone()
    except psycopg.Error as e:
        return f"{name}: cannot connect to {redact(dsn)} ({type(e).__name__})"
    db = row[0] if row else None
    return None if db == want_db else f"{name}: connected to '{db}', expected '{want_db}'"


def _api(url: str | None, client: httpx.Client | None = None) -> str | None:
    if not url:
        return "intel-api: INTEL_API_URL not set"
    c = client or httpx.Client(timeout=15)
    try:
        r = c.get(url.rstrip("/") + "/health")
    except httpx.HTTPError as e:
        return f"intel-api: unreachable ({type(e).__name__}; is the tailnet name resolvable in the container?)"
    return None if r.status_code == 200 else f"intel-api: /health answered {r.status_code}"


def _door(target: str | None, key: Path | None, name: str) -> str | None:
    if not target or not key:
        return f"{name}: target or key not set"
    return None if key.is_file() else f"{name}: key file {key} not found"


def preflight(
    s: Settings,
    m: McpSettings,
    *,
    db: Callable[[str | None, str, str], str | None] = _db,
    api: Callable[[str | None], str | None] = _api,
) -> list[str]:
    """Problems that would make the Sunday run fail or quietly skip a step; empty = ready."""
    problems: list[str] = []
    if s.mip_status_file is None:
        problems.append("MIP_STATUS_FILE not set: the run could not wait for the data platform")
    elif not s.mip_status_file.is_file():
        problems.append(f"data platform status file {s.mip_status_file} not found (mount of /srv/data/mip-weekly?)")
    for f in ("config/keywords/social_content.yaml", "config/content/playbook.yaml", "docs/brand/facts.yaml"):
        if not (s.mip_dir / f).is_file():
            problems.append(f"data platform file {f} not found under {s.mip_dir} (mount of the mip checkout?)")
    problems += [p for p in (db(s.mip_reader_dsn, "mip", "mip"), db(m.intel_rag_admin_dsn, "knowledge db", "rag")) if p]
    problems += [p for p in (api(s.intel_api_url),) if p]
    problems += [
        p
        for p in (
            _door(s.dk_warehouse_ssh, s.dk_warehouse_ssh_key, "door 1 (warehouse tunnel)"),
            _door(s.dk_inbox_ssh, s.dk_inbox_ssh_key, "door 2 (inbox)"),
        )
        if p
    ]
    try:
        d = s.intel_data_dir / "weekly"
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".preflight-write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        problems.append(f"data dir {s.intel_data_dir} not writable ({type(e).__name__})")
    return problems
