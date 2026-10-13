"""Keeping the Mac's disk from filling up (ADR 0042): delete generated videos nothing needs, and old knowledge packs.

Two things are cleaned, and only these:

    reels  out/jobs/<post key>/   a rendered post (video or pictures, spec, manifest)
    packs  data/knowledge/kp-*    old knowledge packs (the active one and the newest few stay: rollback needs them)

A rendered post folder is deleted when nothing needs it any more:
    kept   its post still waits to go out (draft awaiting approval, scheduled, publishing, checking)
    kept   its post's time was less than `keep_days_after_post` days ago
    kept   a run that can still be resumed uses it
    kept   no post knows it, but it is younger than `unknown_keep_days` (just rendered, not handed over yet)
    gone   otherwise: posted or cancelled long ago, or an experiment no post ever used

If the publisher cannot be reached nothing is deleted: guessing which post is still waiting would be the dangerous
kind of wrong. The owner's footage and photos, and `out/campaign`, are never looked at. Pure planning (`plan_*`) and a
separate, small deleting function that refuses any path outside the two folders above.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from intel.studio.config import REPO

LIVE = ("awaiting_approval", "scheduled", "publishing", "checking")  # the post still needs its files
FINAL = ("published", "cancelled", "failed", "expired", "invalid")
RETENTION_FILE = REPO / "config" / "studio" / "retention.yaml"


class RetentionCfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    keep_days_after_post: int = Field(default=3, ge=0, le=60)
    unknown_keep_days: int = Field(default=1, ge=0, le=30)
    keep_packs: int = Field(default=3, ge=1, le=20)
    keep_uploads: int = Field(default=1, ge=0, le=10)


def load_retention(path: Path = RETENTION_FILE) -> RetentionCfg:
    return RetentionCfg.model_validate(yaml.safe_load(path.read_text()) or {}) if path.exists() else RetentionCfg()


@dataclass(frozen=True)
class Item:
    path: Path
    bytes: int
    why: str


@dataclass
class Plan:
    delete: list[Item] = field(default_factory=list)
    keep: list[Item] = field(default_factory=list)
    skipped: str | None = None  # why nothing was planned (e.g. the publisher cannot be reached)

    @property
    def freed(self) -> int:
        return sum(i.bytes for i in self.delete)


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def _local_slot(slot: str) -> dt.datetime:
    when = dt.datetime.fromisoformat(slot)
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def by_post(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """post_key → {live: any variant still needs its files, last: its latest slot}."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r.get("post_key")
        if not key or not r.get("slot"):
            continue
        row = out.setdefault(str(key), {"live": False, "last": None})
        row["live"] = row["live"] or r.get("state") in LIVE or r.get("state") not in FINAL
        slot = _local_slot(str(r["slot"]))
        row["last"] = slot if row["last"] is None else max(row["last"], slot)
    return out


def _unfinished(run: Path) -> bool:
    """A run that can still be continued: no ending in its log, or one that says stopped or failed (Resume works)."""
    last = None
    try:
        for line in (run / "events.jsonl").read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                if e.get("node") == "orchestrator" and e.get("kind") in ("start", "end"):
                    last = e
    except (OSError, ValueError):
        return False
    return last is not None and (last["kind"] == "start" or last.get("status") in ("stopped", "failed"))


def protected_by_runs(runs_dir: Path, now: dt.datetime, days: int = 2) -> set[str]:
    """Post folders a recent run that can still be resumed points at (spec or manifest paths). A finished run needs
    nothing: its posts are the publisher's business."""
    out: set[str] = set()
    for state in runs_dir.glob("*/state.json"):
        try:
            if now - dt.datetime.fromtimestamp(state.stat().st_mtime, dt.UTC) > dt.timedelta(days=days):
                continue
            if not _unfinished(state.parent):
                continue
            ideas = json.loads(state.read_text()).get("ideas") or {}
        except (OSError, ValueError):
            continue
        for row in ideas.values():
            for key in ("spec_path", "manifest"):
                if row.get(key):
                    out.add(Path(str(row[key])).parent.name)
    return out


def plan_jobs(jobs_dir: Path, posts: dict[str, dict[str, Any]], protected: set[str], now: dt.datetime,
              cfg: RetentionCfg) -> Plan:  # fmt: skip
    plan = Plan()
    if not jobs_dir.is_dir():
        return plan
    for folder in sorted(p for p in jobs_dir.iterdir() if p.is_dir() and not p.is_symlink()):
        size = size_of(folder)
        post = posts.get(folder.name)
        age = now - dt.datetime.fromtimestamp(folder.stat().st_mtime, dt.UTC)
        if folder.name in protected:
            plan.keep.append(Item(folder, size, "a run that can still be resumed uses it"))
        elif post is not None and post["live"]:
            plan.keep.append(Item(folder, size, "its post still waits to go out"))
        elif post is not None and now - post["last"] < dt.timedelta(days=cfg.keep_days_after_post):
            plan.keep.append(Item(folder, size, f"posted less than {cfg.keep_days_after_post} days ago"))
        elif post is None and age < dt.timedelta(days=cfg.unknown_keep_days):
            plan.keep.append(Item(folder, size, "new: not handed over yet"))
        elif post is not None:
            plan.delete.append(Item(folder, size, "its post is finished and past the keep time"))
        else:
            plan.delete.append(Item(folder, size, "no post uses it (a test or an old plan)"))
    return plan


def plan_packs(pack_dir: Path, keep: int) -> Plan:
    plan = Plan()
    if not pack_dir.is_dir():
        return plan
    active = (pack_dir / "ACTIVE").read_text().strip() if (pack_dir / "ACTIVE").exists() else None
    packs = sorted(p for p in pack_dir.glob("kp-*") if p.is_dir() and not p.is_symlink())
    newest = {p.name for p in packs[-keep:]}
    for p in packs:
        size = size_of(p)
        if p.name == active:
            plan.keep.append(Item(p, size, "the active pack"))
        elif p.name in newest:
            plan.keep.append(Item(p, size, f"one of the {keep} newest"))
        else:
            plan.delete.append(Item(p, size, "an old pack (it can be rebuilt from its pull)"))
    return plan


def plan_uploads(upload_dir: Path, keep: int) -> Plan:
    """Copies of what was sent to the VPS (data/packages/knowledge): the newest knowledge pack stays, older ones go. The
    model bundle (about 1 GB) is only needed at the moment it is sent: `pack ship --models` makes it again."""
    plan = Plan()
    if not upload_dir.is_dir():
        return plan
    files = sorted(p for p in upload_dir.glob("knowledge-*.tar.gz*") if p.is_file() and not p.is_symlink())
    stems = sorted({p.name.removesuffix(".sha256") for p in files if p.name.startswith("knowledge-pack-")})
    newest = set(stems[-keep:]) if keep else set()  # a copy and its checksum file are one thing
    for p in files:
        size = p.stat().st_size
        if p.name.removesuffix(".sha256") in newest:
            plan.keep.append(Item(p, size, "the newest copy sent to the VPS"))
        elif p.name.startswith("knowledge-models-"):
            plan.delete.append(Item(p, size, "the model bundle was sent once; `pack ship --models` rebuilds it"))
        else:
            plan.delete.append(Item(p, size, "an older copy of what was sent to the VPS"))
    return plan


def delete(item: Item, roots: tuple[Path, ...]) -> bool:
    """Remove one planned folder. Refuses anything that is not a real folder directly inside one of `roots`."""
    p = item.path
    if p.is_symlink() or p.resolve().parent not in {r.resolve() for r in roots}:
        return False
    if p.is_dir():
        shutil.rmtree(p)
    elif p.is_file():
        p.unlink()
    else:
        return False
    return True


def human(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


STATES = (
    "awaiting_approval",
    "scheduled",
    "publishing",
    "checking",
    "published",
    "cancelled",
    "failed",
    "expired",
    "invalid",
)  # every state a post can be in: all asked for, so a late draft is never mistaken for an unknown file


def gather(client: Any) -> list[dict[str, Any]] | None:
    """Every studio post the publisher knows, by state. None when it cannot be reached (then nothing is deleted)."""
    rows: list[dict[str, Any]] = []
    try:
        for state in STATES:
            rows += client.calendar(source="api", state=state, limit=200).get("posts") or []
    except Exception:  # noqa: BLE001 - not knowing is a reason to delete nothing
        return None
    return rows


@dataclass
class Result:
    jobs: Plan
    packs: Plan
    uploads: Plan = field(default_factory=Plan)
    applied: bool = False
    freed: int = 0
    removed: int = 0


def run(cfg: RetentionCfg, client: Any, reels: Path, data_dir: Path, now: dt.datetime, apply: bool = False,
        pack_dir: Path | None = None, working: bool = False) -> Result:  # fmt: skip
    """Plan (and with `apply`, do) the cleanup. `client` is the publisher; `reels` the reels repo; `data_dir` the studio
    data folder (its runs say what a resumable run still needs). `working`: a run is working right now, so no video is
    touched (it may be about to hand one over)."""
    rows = gather(client) if client is not None else None
    jobs_dir = reels / "out" / "jobs"
    if working:
        jobs = Plan(skipped="a run is working: its videos are not touched now")
    elif rows is None:
        jobs = Plan(skipped="the publisher cannot be reached, so I cannot tell which videos a post still needs")
    else:
        jobs = plan_jobs(jobs_dir, by_post(rows), protected_by_runs(data_dir / "runs", now), now, cfg)
    packs_dir = packs_root(pack_dir, data_dir)
    uploads_dir = data_dir.parent / "packages" / "knowledge"
    packs = plan_packs(packs_dir, cfg.keep_packs)
    uploads = plan_uploads(uploads_dir, cfg.keep_uploads)
    result = Result(jobs, packs, uploads)
    if apply:
        result.applied = True
        work = [(i, (jobs_dir,)) for i in jobs.delete] + [(i, (packs_dir,)) for i in packs.delete]
        work += [(i, (uploads_dir,)) for i in uploads.delete]
        for item, roots in work:
            size = item.bytes
            if delete(item, roots):
                result.freed += size
                result.removed += 1
    return result


def packs_root(pack_dir: Path | None, data_dir: Path) -> Path:
    return pack_dir if pack_dir is not None else data_dir.parent / "knowledge"
