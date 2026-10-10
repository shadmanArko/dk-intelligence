"""Build, activate and inspect knowledge packs (architecture §4.1). P4.1 builds the brand part only.

A pack is a folder `data/knowledge/<pack_id>/` that never changes after it is written:
- `brand/`: copies of the data platform's brand book, voice and rules, facts and playbook, plus the market
  snapshot's content_rules.yaml;
- `manifest.json`: every file's sha256, the sources (market snapshot id) and the build time.
`data/knowledge/ACTIVE` names the pack the servers read. Activation is a one-line atomic file swap.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

BRAND_FILES: dict[str, str] = {  # name in the pack -> path in the data platform repo
    "brand-book.md": "docs/brand/brand-book.md",
    "voice-and-rules.md": "docs/brand/voice-and-rules.md",
    "facts.yaml": "docs/brand/facts.yaml",
    "playbook.yaml": "config/content/playbook.yaml",
}
TRENDS = "tables/trends.parquet"
# origin-group labels (some name a religion) never enter a pack: rule 10, decision D2
TREND_TAG_TYPES_OUT = ("community",)
ACTIVE = "ACTIVE"
MANIFEST = "manifest.json"


class PackError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest_market_snapshot(market_dir: Path) -> Path:
    latest = market_dir / "LATEST"
    if not latest.exists():
        raise PackError(f"no market snapshot: {latest} is missing (run `intel market pull`)")
    snap = market_dir / latest.read_text().strip()
    if not (snap / "content_rules.yaml").exists():
        raise PackError(f"{snap} has no content_rules.yaml")
    return snap


def rice_dish_labels(mip_dir: Path) -> tuple[dict[str, str], str | None]:
    """Wikidata id -> English name (else any name) from the data platform's newest rice-dish file, and its sha256.
    Trend tags of type rice_dish are Wikidata ids ("Q46383"); clients need the name."""
    files = sorted((mip_dir / "data" / "bulk" / "wikidata").glob("rice_dishes_*.json"))
    if not files:
        return {}, None
    entities = json.loads(files[-1].read_text())["entities"]
    labels = {}
    for qid, e in entities.items():
        names = e.get("labels") or {}
        best = names.get("en") or names.get("de") or next(iter(names.values()), None)
        if best:
            labels[qid] = str(best["value"])
    return labels, sha256(files[-1])


Indexer = Callable[[Path, Path, dict[str, Path]], dict[str, Any]]
"""(target index dir, market snapshot dir, brand sources by name) -> manifest extras; writes chunks and vectors."""


def build_brand_pack(
    mip_dir: Path,
    market_dir: Path,
    pack_root: Path,
    now: dt.datetime | None = None,
    indexer: Indexer | None = None,
) -> str:
    """Copy the brand sources into a new pack (and, with an indexer, its search index); returns its id.
    Never overwrites an existing pack."""
    snap = latest_market_snapshot(market_dir)
    sources = {name: mip_dir / rel for name, rel in BRAND_FILES.items()}
    sources["content_rules.yaml"] = snap / "content_rules.yaml"
    missing = [str(p) for p in sources.values() if not p.exists()]
    if missing:
        raise PackError("missing brand sources: " + ", ".join(missing))
    hashes = {f"brand/{name}": sha256(p) for name, p in sorted(sources.items())}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:6]
    at = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    pack_id = f"kp-{at:%Y%m%dT%H%M%S}-{digest}"
    pack_root.mkdir(parents=True, exist_ok=True)
    final = pack_root / pack_id
    if final.exists():
        raise PackError(f"{pack_id} already exists")
    tmp = Path(tempfile.mkdtemp(prefix=".build-", dir=pack_root))
    try:
        (tmp / "brand").mkdir()
        for name, src in sources.items():
            shutil.copyfile(src, tmp / "brand" / name)
        copied = {f"brand/{name}": sha256(tmp / "brand" / name) for name in sources}
        if copied != hashes:
            raise PackError("a source changed while it was being copied; build again")
        extras: dict[str, Any] = {}
        files = dict(hashes)
        if (snap / "content_feature_performance.parquet").exists():
            import pandas as pd

            trends = pd.read_parquet(snap / "content_feature_performance.parquet")
            trends = trends[~trends["tag_type"].isin(TREND_TAG_TYPES_OUT)].reset_index(drop=True)
            labels, labels_sha = rice_dish_labels(mip_dir)
            trends["tag_label"] = [
                labels.get(t, t) if kind == "rice_dish" else t.replace("_", " ")
                for t, kind in zip(trends["tag_id"], trends["tag_type"], strict=True)
            ]
            (tmp / "tables").mkdir()
            trends.to_parquet(tmp / TRENDS, index=False)
            files[TRENDS] = sha256(tmp / TRENDS)
            extras["trends"] = {
                "rows": len(trends),
                "excluded_tag_types": list(TREND_TAG_TYPES_OUT),
                "rice_dish_labels_sha256": labels_sha,
            }
        if indexer is not None:
            (tmp / "index").mkdir()
            extras.update(indexer(tmp / "index", snap, {n: tmp / "brand" / n for n in sources}))
            files.update({f"index/{f.name}": sha256(f) for f in sorted((tmp / "index").iterdir())})
        manifest = {
            **extras,
            "pack_id": pack_id,
            "kind": "full" if indexer is not None else "brand_only",
            "built_at": at.isoformat(timespec="seconds"),
            "market_snapshot": snap.name,
            "files": files,
        }
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True))
        for sub in ("brand", "index", "tables"):
            for f in (tmp / sub).iterdir() if (tmp / sub).exists() else []:
                f.chmod(0o444)
        tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return pack_id


def verify(pack_dir: Path) -> dict[str, Any]:
    """The manifest, after checking every file against it; raises PackError on any mismatch."""
    path = pack_dir / MANIFEST
    if not path.exists():
        raise PackError(f"{pack_dir.name}: no manifest")
    manifest: dict[str, Any] = json.loads(path.read_text())
    for rel, expected in manifest["files"].items():
        f = pack_dir / rel
        if not f.exists() or sha256(f) != expected:
            raise PackError(f"{pack_dir.name}: {rel} is missing or changed")
    return manifest


def activate(pack_root: Path, pack_id: str) -> None:
    verify(pack_root / pack_id)
    tmp = pack_root / f".{ACTIVE}.tmp"
    tmp.write_text(pack_id + "\n")
    os.replace(tmp, pack_root / ACTIVE)


def active_id(pack_root: Path) -> str | None:
    f = pack_root / ACTIVE
    return f.read_text().strip() or None if f.exists() else None


def status(pack_root: Path) -> dict[str, Any]:
    packs = sorted(p.name for p in pack_root.glob("kp-*") if p.is_dir()) if pack_root.exists() else []
    return {"active": active_id(pack_root), "packs": packs}
