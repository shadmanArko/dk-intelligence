"""Validate a snapshot against its contracts and describe it in a data card (ADR 0017).

`validate_snapshot(snapshot_dir, source, previous_dir)` returns a `QualityReport`:
- `findings`: every broken expectation, as error (blocks training) or warning (reported);
- `card`: per contracted table: rows and the change vs the previous snapshot, freshness, and for each required column
  the share of nulls, distinct values, range or most common values.

The report is written to `data/quality/<source>-<snapshot id>.json` next to (never inside) the immutable snapshot.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pandera.pandas as pa
import pyarrow.parquet as pq

from intel.quality.contracts import Contracts, Finding, Link, Source, TableContract

MAX_EXAMPLES = 3


@dataclass
class QualityReport:
    source: Source
    snapshot_id: str
    previous_id: str | None
    snapshot_at: str
    findings: list[Finding] = field(default_factory=list)
    card: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self, limit: int = 10) -> dict[str, Any]:
        """Compact form for the weekly run report (the VPS stores and shows it)."""
        return {
            "snapshot": self.snapshot_id,
            "tables": len(self.card),
            "errors": [f.line()[:200] for f in self.errors[:limit]],
            "warnings": [f.line()[:200] for f in self.warnings[:limit]],
        }

    def to_json(self) -> str:
        d = asdict(self)
        d["ok"] = self.ok
        return json.dumps(d, indent=1, default=str)

    def save(self, quality_dir: Path) -> Path:
        quality_dir.mkdir(parents=True, exist_ok=True)
        path = quality_dir / f"{self.source}-{self.snapshot_id}.json"
        path.write_text(self.to_json() + "\n")
        return path


def _manifest(snapshot_dir: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((snapshot_dir / "manifest.json").read_text())
    return data


def _profile(s: pd.Series) -> dict[str, Any]:
    out: dict[str, Any] = {"null_pct": round(float(s.isna().mean()) * 100, 2) if len(s) else 0.0}
    v = s.dropna()
    if v.empty:
        return out
    if pd.api.types.is_numeric_dtype(v) and not pd.api.types.is_bool_dtype(v):
        out.update(min=float(v.min()), max=float(v.max()), mean=round(float(v.mean()), 4))
    elif pd.api.types.is_datetime64_any_dtype(v):
        out.update(min=str(v.min()), max=str(v.max()))
    else:
        try:
            distinct = int(v.nunique())
        except TypeError:  # lists / arrays: not hashable
            return out
        out["distinct"] = distinct
        if distinct <= 12:
            out["top"] = {str(k): int(n) for k, n in v.value_counts().head(12).items()}
    return out


def _schema_findings(table: str, schema: pa.DataFrameSchema | None, df: pd.DataFrame, severity: str) -> list[Finding]:
    if schema is None or df.empty:
        return []
    try:
        schema.validate(df, lazy=True)
        return []
    except pa.errors.SchemaErrors as e:
        fc = e.failure_cases
        out = []
        for (column, check), g in fc.groupby(["column", "check"], dropna=False, sort=True):
            examples = ", ".join(str(x)[:40] for x in g["failure_case"].head(MAX_EXAMPLES))
            col = "" if pd.isna(column) else f"{column} "
            out.append(Finding(table, f"{col}{check}", severity, f"{len(g)} row(s), e.g. {examples}"))  # type: ignore[arg-type]
        return out


def _ts(x: Any) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t


def check_table(
    c: TableContract, snapshot_dir: Path, manifest: dict[str, Any], prev: dict[str, Any] | None
) -> tuple[list[Finding], dict[str, Any], pd.DataFrame | None]:
    t = c.table
    info = manifest["tables"].get(t)
    if info is None:
        return [Finding(t, "present", "error", "table missing from the snapshot")], {"missing": True}, None
    findings: list[Finding] = []
    have = {col["name"]: col["arrow"] for col in info.get("columns", [])}
    for col, want in c.columns.items():
        if col not in have:
            findings.append(Finding(t, f"{col} present", "error", "required column missing"))
        elif have[col] != want:
            findings.append(Finding(t, f"{col} type", "error", f"is {have[col]}, expected {want}"))
    rows = int(info["rows"])
    card: dict[str, Any] = {"rows": rows, "used_by": list(c.used_by)}
    if rows < c.min_rows:
        findings.append(Finding(t, "min_rows", "error", f"{rows} rows, expected at least {c.min_rows}"))
    prev_rows = (prev or {}).get("tables", {}).get(t, {}).get("rows")
    if prev_rows is not None:
        card["rows_previous"] = int(prev_rows)
        if c.max_drop is not None and prev_rows > 0 and rows < (1 - c.max_drop) * prev_rows:
            findings.append(
                Finding(
                    t,
                    "volume",
                    "error",
                    f"{rows} rows vs {prev_rows} in the previous snapshot (more than {c.max_drop:.0%} fewer)",
                )
            )
    if any(f.severity == "error" and f.check.endswith(("present", "type")) for f in findings):
        return findings, card, None  # the data cannot be read the way the models read it
    present = [col for col in c.columns if col in have]
    df = pq.read_table(snapshot_dir / info["file"], columns=present).to_pandas()
    findings += _schema_findings(t, c.errors, df, "error")
    findings += _schema_findings(t, c.warnings, df, "warning")
    snap_at = _ts(manifest["snapshot_at"])
    if c.freshness and rows:
        newest = df[c.freshness.column].max()
        if pd.notna(newest):
            age = snap_at - _ts(newest)
            card["newest"] = str(newest)
            card["age_hours"] = round(age.total_seconds() / 3600, 1)
            fr = c.freshness
            if fr.error_after is not None and age > fr.error_after:
                findings.append(Finding(t, "freshness", "error", f"newest {fr.column} is {_human(age)} old"))
            elif age > fr.warn_after:
                findings.append(Finding(t, "freshness", "warning", f"newest {fr.column} is {_human(age)} old"))
    if c.horizon and rows:
        col, ahead = c.horizon
        last = _ts(df[col].max())
        if last < snap_at + ahead:
            findings.append(
                Finding(t, "horizon", "error", f"{col} ends {last.date()}, needs to reach {(snap_at + ahead).date()}")
            )
    future = [col for col, typ in c.columns.items() if typ.startswith("timestamp") and col in df]
    for col in future:  # a timestamp after the snapshot itself is impossible for events (posts, metrics, reviews)
        n = int((df[col].dropna().map(_ts) > snap_at + dt.timedelta(hours=1)).sum())
        if n:
            findings.append(Finding(t, f"{col} not in the future", "error", f"{n} row(s) after the snapshot time"))
    card["columns"] = {col: _profile(df[col]) for col in present}
    return findings, card, df


def _human(d: pd.Timedelta | dt.timedelta) -> str:
    h = d.total_seconds() / 3600
    return f"{h:.0f} h" if h < 48 else f"{h / 24:.1f} days"


def check_link(lk: Link, frames: dict[str, pd.DataFrame]) -> list[Finding]:
    child, parent = frames.get(lk.child), frames.get(lk.parent)
    if child is None or parent is None or lk.column not in child or lk.parent_column not in parent:
        return []
    values = child[lk.column].dropna()
    orphans = values[~values.isin(set(parent[lk.parent_column].dropna()))]
    if orphans.empty:
        return []
    ex = ", ".join(str(x)[:12] for x in orphans.unique()[:MAX_EXAMPLES])
    return [
        Finding(
            lk.child,
            f"{lk.column} -> {lk.parent}.{lk.parent_column}",
            lk.severity,
            f"{len(orphans)} row(s) point to nothing, e.g. {ex}",
        )
    ]


def validate_snapshot(snapshot_dir: Path, source: Source, previous_dir: Path | None = None) -> QualityReport:
    manifest = _manifest(snapshot_dir)
    prev = _manifest(previous_dir) if previous_dir and (previous_dir / "manifest.json").exists() else None
    contracts = Contracts.for_source(source)
    rep = QualityReport(
        source, snapshot_dir.name, previous_dir.name if prev and previous_dir else None, str(manifest["snapshot_at"])
    )
    frames: dict[str, pd.DataFrame] = {}
    for name, c in contracts.tables.items():
        findings, card, df = check_table(c, snapshot_dir, manifest, prev)
        rep.findings += findings
        rep.card[name] = card
        if df is not None:
            frames[name] = df
    for lk in contracts.links:
        rep.findings += check_link(lk, frames)
    rep.findings.sort(key=lambda f: (f.severity != "error", f.table, f.check))
    return rep
