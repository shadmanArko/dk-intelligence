"""The media step of the lab: from a planned slot to a rendered, checked job ready for handover. Plain code.

The designer agent (AI) writes the CREATIVE part of a ContentSpec: hook, shots, slides, on-screen words. Everything an
AI must not control is set here by code: the job id, the format and size the platform needs, the slot time, the post
captions (written and checked by the writers), and the footage used recently. Rendering is the reels engine's own CLI
(`npm run job`): exit codes and a JSON last line are a complete contract, and a render is minutes of CPU that no
agent can loop on.

    kind_for / job_id / designer_task / finalize_spec   pure
    reels_job / AssetLedger                              the only I/O (a subprocess and one JSON file)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

LAB = Path(__file__).resolve().parent
REPO = LAB.parents[1]
REELS = Path(
    os.environ.get("DK_REELS_DIR") or REPO.parent / "dhaka-kacchi-reels"
)  # a sibling folder; the path has spaces
LEDGER = REPO / "data" / "lab" / "asset-ledger.json"
SHORT = {"instagram": "ig", "facebook": "fb", "tiktok": "tt", "youtube": "yt", "threads": "th"}
RECENT_JOBS = 12  # footage used in the last 12 jobs is avoided when something else fits
RENDER_TIMEOUT_S = 1200

# what the designer is told it may make, by the planner's slot format
KINDS = {"reel": "video", "video": "video", "short": "video", "carousel": "carousel", "image": "image"}


def kind_for(slot_format: Any) -> str:
    """video (9:16 reel) · carousel (3:4 swipe) · image (one 4:5 picture)."""
    return KINDS.get(str(slot_format), "image")


def job_id(platform: str, slot_id: str, planned_posted_at: str, suffix: str = "") -> str:
    """Stable, readable and valid for both systems: 2026w42-ig-tue1700-s1. The same slot in the same week is the same
    job, so a re-run skips it instead of duplicating. A rejected post can't reuse its key: pass suffix="r2" to redo."""
    when = dt.datetime.fromisoformat(planned_posted_at)
    year, week, _ = when.isocalendar()
    slot = re.sub(r"[^a-z0-9]", "", str(slot_id).lower()) or "x"
    tail = f"-{re.sub(r'[^a-z0-9]', '', suffix.lower())}" if suffix else ""
    return f"{year}w{week:02d}-{SHORT.get(platform, platform[:2])}-{when:%a%H%M}-{slot}{tail}".lower()


def compact_inventory(inv: dict[str, dict[str, dict[str, int]]]) -> dict[str, dict[str, str]]:
    """`{"video": {"Pot opening": {"n": 12, "star2": 3}}}` → `{"video": {"Pot opening": "12, 3 key moments"}}`."""
    out: dict[str, dict[str, str]] = {}
    for kind, cats in inv.items():
        out[kind] = {}
        for cat, c in sorted(cats.items()):
            tag = f"{c['n']}"
            if c.get("star2"):
                tag += f", {c['star2']} key moments"
            elif c.get("star1"):
                tag += f", {c['star1']} best takes"
            out[kind][cat] = tag
    return out


def designer_task(
    *,
    jid: str,
    platform: str,
    slot_format: str,
    planned_posted_at: str,
    angle: str,
    caption: str,
    evidence: list[dict[str, str]],
    brand: list[str],
    vocab: dict[str, Any],
    inventory: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Everything the designer needs, worked out by code so it spends no turns looking things up."""
    kind = kind_for(slot_format)
    return {
        "job_id": jid,
        "make": kind,
        "platform": platform,
        "planned_posted_at": planned_posted_at,
        "angle": angle,
        "caption": caption,  # already written and checked: the on-screen words should sit with it, not repeat it
        "evidence": evidence,
        "brand": brand,
        "footage_categories": inventory["video" if kind == "video" else "photo"],
        "music": {"cultures": vocab.get("music_cultures", []), "moods": vocab.get("music_moods", [])},
        "slide_templates": list((vocab.get("templates", {}).get("slides") or {}).keys()) if kind != "video" else [],
    }


def finalize_spec(
    answer: dict[str, Any] | None,
    *,
    jid: str,
    platform: str,
    slot_format: str,
    planned_posted_at: str,
    caption: str,
    avoid: list[str],
    week_label: str,
) -> tuple[dict[str, Any] | None, str]:
    """The designer's answer is data: take its creative content, then set everything else from code. Returns
    (spec, "") or (None, why). Nothing the designer wrote for the envelope, scheduling or captions is trusted."""
    if not isinstance(answer, dict) or not isinstance(answer.get("spec"), dict):
        return None, "the designer returned no spec"
    content = answer["spec"].get("content")
    if not isinstance(content, dict):
        return None, "the spec has no content"
    kind = kind_for(slot_format)
    if kind == "video":
        if content.get("kind") != "video":
            return None, f"a {slot_format} needs a video spec, the designer made '{content.get('kind')}'"
        content = {**content, "template": "beat-reel", "formats": ["9:16"]}  # the platforms take 9:16; code decides
    else:
        if content.get("kind") != "slides" or not isinstance(content.get("slides"), list) or not content["slides"]:
            return None, f"a {slot_format} needs a slides spec, the designer made '{content.get('kind')}'"
        if kind == "carousel":
            content = {**content, "size": "carousel"}
        else:  # one picture in the feed
            content = {**content, "size": "album", "slides": content["slides"][:1]}
    language = answer["spec"].get("language")
    spec: dict[str, Any] = {
        "spec_version": 1,
        "job_id": jid,
        "campaign": week_label,
        "language": language
        if isinstance(language, list) and all(isinstance(x, str) for x in language)
        else ["en", "de"],
        "content": content,
        "captions": {platform: caption},
        "publish": {"platforms": [platform], "at": planned_posted_at},
    }
    if avoid:
        spec["avoid_assets"] = avoid
    return spec, ""


# ---------- I/O ----------


def reels_job(spec_path: Path, *flags: str, reels: Path | None = None) -> tuple[int, dict[str, Any]]:
    """Run the reels engine on a spec file. Returns (exit code, the JSON summary it prints as its last line).
    0 ok · 2 invalid spec · 3 render error · 4 QA failed. A crash or a missing engine is code 3 with the reason."""
    root = reels or REELS
    if not (root / "package.json").exists():
        return 3, {"ok": False, "error": f"the reels engine is not at {root} (set DK_REELS_DIR)"}
    try:
        p = subprocess.run(["npm", "run", "-s", "job", "--", str(spec_path), *flags], cwd=root, capture_output=True,
                           text=True, timeout=RENDER_TIMEOUT_S)  # fmt: skip
    except subprocess.TimeoutExpired:
        return 3, {"ok": False, "error": f"the render took longer than {RENDER_TIMEOUT_S} s"}
    lines = [x for x in p.stdout.splitlines() if x.strip()]
    try:
        return p.returncode, json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        return 3, {"ok": False, "error": (p.stderr or p.stdout)[-300:] or f"exit {p.returncode}, no summary"}


def thumbnail(manifest: dict[str, Any], folder: Path, out: Path) -> bool:
    """A small picture of a render for the console: a frame from 40% into a video, or the first slide. Best effort."""
    first = (manifest.get("files") or [{}])[0]
    if not first.get("path"):
        return False
    src = folder / first["path"]
    seek = ["-ss", f"{float(first['duration_s']) * 0.4:.2f}"] if first.get("duration_s") else []
    try:
        cmd = ["ffmpeg", "-v", "error", "-y", *seek, "-i", str(src), "-frames:v", "1", "-vf", "scale=360:-1", str(out)]
        r = subprocess.run(cmd, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and out.exists()


class AssetLedger:
    """Which footage the last jobs used, so the next ones prefer something else (the resolver stays pure: the list
    travels in the spec as `avoid_assets`)."""

    def __init__(self, path: Path | None = None):
        self.path = path or LEDGER
        self.rows: list[dict[str, Any]] = json.loads(self.path.read_text()) if self.path.exists() else []

    def recent(self) -> list[str]:
        seen: list[str] = []
        for row in self.rows[-RECENT_JOBS:]:
            seen += [a for a in row["assets"] if a not in seen]
        return seen

    def record(self, jid: str, assets: list[str], now: dt.datetime | None = None) -> None:
        self.rows = [r for r in self.rows if r["job_id"] != jid] + [
            {"job_id": jid, "at": (now or dt.datetime.now(dt.UTC)).isoformat(timespec="seconds"), "assets": assets}
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rows[-200:], indent=1))


def assets_used(manifest: dict[str, Any]) -> list[str]:
    """What a job used, for the ledger: each asset id and its clip (the file name without extension), because the
    resolver avoids by either, and one clip has several moments."""
    plan = manifest.get("plan") or {}
    out: list[str] = []
    for p in plan.get("shots") or plan.get("slides") or []:
        for x in (p.get("asset"), Path(str(p["src"])).stem if p.get("src") else None):
            if x and str(x) not in out:
                out.append(str(x))
    return out
