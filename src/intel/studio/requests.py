"""Style requests: what the creative director and the designer say no template could do. A plain append-only file the
owner (or a Claude Code session working in the reels repo) reads to decide which template to build next. Agents never
write TypeScript (ADR 0031): the loop is request → a person builds a template under the reels gates → the registry
grows."""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path
from typing import Any

from intel.studio.config import REPO

PATH = REPO / "data" / "studio" / "style-requests.jsonl"
_lock = threading.Lock()


def add(idea_id: str, by: str, text: str, idea_format: str, path: Path | None = None) -> None:
    row = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "idea": idea_id, "by": by,
           "format": idea_format,
           "request": text[:300]}  # fmt: skip
    target = path or PATH
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or PATH
    return [json.loads(x) for x in target.read_text().splitlines()] if target.exists() else []


def tally(rows: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """The same wish said again and again is the template to build: counted by its first words."""
    counts: dict[str, int] = {}
    for r in rows:
        key = " ".join(str(r["request"]).lower().split()[:6])
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])
