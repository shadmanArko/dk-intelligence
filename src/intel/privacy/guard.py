"""What a pull may contain (ADR 0024). Runs inside `run_pull` on the half-written pull, before checksums and the rename:

1. every table must be declared in the policy (purpose limitation; an undeclared table fails the pull);
2. third-party free text marked `redact` has emails and phone numbers replaced (biryani review texts);
3. the PII scan: findings in a table declared `personal: declared` are recorded; anywhere else they fail the pull.

The result is the manifest's `privacy` section: policy version, redactions and declared findings (counts only).
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from intel.privacy import PrivacyViolation, policy, tables
from intel.privacy.pii import Finding, redact, scan_table


def _sha256(path: Path) -> str:
    from intel.pull import sha256_file

    return sha256_file(path)


def redact_columns(path: Path, columns: list[str]) -> dict[str, int]:
    """Rewrite `columns` of a Parquet file with emails / phone numbers replaced; returns replacements per column."""
    t = pq.read_table(path)
    counts: dict[str, int] = {}
    for c in columns:
        if c not in t.column_names:
            continue
        n_total = 0
        out: list[str | None] = []
        for v in t.column(c).to_pylist():
            if v is None:
                out.append(None)
                continue
            new, n = redact(v)
            n_total += n
            out.append(new)
        counts[c] = n_total
        if n_total:
            i = t.column_names.index(c)
            t = t.set_column(i, t.schema.field(c), pa.array(out, type=t.schema.field(c).type))
    if any(counts.values()):
        pq.write_table(t, path, compression="zstd")
    return counts


def check(section: str, pull_dir: Path, names: list[str]) -> dict[str, Any]:
    """Redact, scan and judge the tables `names` in `pull_dir`. Raises PrivacyViolation; returns the manifest part."""
    declared = tables(section)
    undeclared = sorted(set(names) - set(declared))
    if undeclared:
        raise PrivacyViolation(
            f"undeclared tables {', '.join(undeclared)}: add them to src/intel/privacy/policy.yaml ({section}) "
            "with a purpose before pulling them"
        )
    redacted: dict[str, dict[str, int]] = {}
    findings: list[Finding] = []
    for name in names:
        path = pull_dir / f"{name}.parquet"
        cols = declared[name].get("redact") or []
        if cols:
            redacted[name] = redact_columns(path, cols)
        findings += scan_table(path, name)
    blocking = [f for f in findings if declared[f.table].get("personal") != "declared"]
    if blocking:
        raise PrivacyViolation(
            "personal data the policy does not declare: "
            + "; ".join(f"{f.table}.{f.column}: {f.count} x {f.kind}" for f in blocking)
            + " (counts only; fix the source or declare it with a purpose in src/intel/privacy/policy.yaml)"
        )
    return {
        "policy_version": policy()["version"],
        "section": section,
        "redacted": {t: c for t, c in redacted.items() if any(c.values())},
        "declared_findings": [asdict(f) for f in findings],
    }


def pull_guard(section: str) -> Callable[[Path, list[Any]], dict[str, Any]]:
    """The `guard` for `run_pull`: checks the written tables and refreshes checksums of any it rewrote."""

    def guard(pull_dir: Path, results: list[Any]) -> dict[str, Any]:
        part = check(section, pull_dir, [r.table for r in results])
        for r in results:
            if r.table in part["redacted"]:
                path = pull_dir / r.file
                r.sha256, r.bytes = _sha256(path), path.stat().st_size
        return part

    return guard


def minimise_existing(pull_dir: Path, section: str, now: dt.datetime | None = None) -> dict[str, Any]:
    """Bring a pull made before the policy in line with it: delete undeclared tables, redact, re-scan, re-checksum.

    Snapshots stay immutable in everything a model reads: only undeclared tables are removed and only `redact` columns
    rewritten; the manifest records what changed and the original checksums."""
    manifest = json.loads((pull_dir / "manifest.json").read_text())
    if manifest.get("privacy"):
        return {"pull": pull_dir.name, "skipped": "already minimised"}
    declared = tables(section)
    original = {t: v["sha256"] for t, v in manifest["tables"].items()}
    removed = sorted(t for t in manifest["tables"] if t not in declared)
    for t in removed:
        (pull_dir / manifest["tables"][t]["file"]).unlink(missing_ok=True)
        del manifest["tables"][t]
    part = check(section, pull_dir, sorted(manifest["tables"]))
    for t in part["redacted"]:
        path = pull_dir / manifest["tables"][t]["file"]
        manifest["tables"][t].update(sha256=_sha256(path), bytes=path.stat().st_size)
    manifest["total_rows"] = sum(v["rows"] for v in manifest["tables"].values())
    manifest["privacy"] = {
        **part,
        "minimised_at": (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds"),
        "removed_tables": removed,
        "original_sha256": {t: original[t] for t in removed + sorted(part["redacted"])},
    }
    tmp = pull_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    shutil.move(tmp, pull_dir / "manifest.json")
    files = [v["file"] for v in manifest["tables"].values()] + list(manifest.get("files") or [])
    sums = [f"{_sha256(pull_dir / f)}  {f}" for f in sorted(files)]
    sums.append(f"{_sha256(pull_dir / 'manifest.json')}  manifest.json")
    (pull_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return {"pull": pull_dir.name, "removed_tables": removed, "redacted": part["redacted"]}
