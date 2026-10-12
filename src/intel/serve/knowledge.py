"""intel-api's knowledge intake (ADR 0030): packs and serving models for intel-mcp arrive through the same upload-only
inbox as model packages. Each new package is verified and installed into the knowledge volume once (by its outer
checksum); intel-mcp reads that volume read-only and syncs its database to `packs/ACTIVE`."""

from __future__ import annotations

from pathlib import Path

from intel.rag.ship import PREFIX, Intake, intake
from intel.serve.db import kv_get, kv_set, log_event


def scan_knowledge(dsn: str, inbox: Path, knowledge: Path) -> list[Intake]:
    out = []
    for side in sorted(inbox.glob(f"{PREFIX}*.tar.gz.sha256")):
        tgz = side.with_name(side.name.removesuffix(".sha256"))
        try:
            outer = side.read_text().split()[0]
        except (OSError, IndexError):
            continue
        key = f"knowledge:{outer}"
        if not tgz.exists() or kv_get(dsn, key):
            continue
        result = intake(tgz, knowledge)
        kv_set(dsn, key, result.status)
        log_event(dsn, "knowledge_intake", file=result.file, package_kind=result.kind, id=result.ident,
                  status=result.status, problems=result.problems)  # fmt: skip
        out.append(result)
    return out


def knowledge_message(it: Intake) -> str | None:
    """Telegram text: only what the owner should know (a refused package, or a rollback that happened)."""
    if it.status == "invalid":
        return f"⚠️ Knowledge package {it.file} was refused: {'; '.join(it.problems)[:300]}"
    if it.kind == "activate":
        return f"↩️ Knowledge pack {it.ident} is active again (rollback)."
    return None
