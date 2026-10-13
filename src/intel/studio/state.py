"""The run's memory on disk: `state.json` in the run folder, written atomically after every change.

A stopped run (a crash, the Mac sleeping, Ctrl-C) resumes with `intel studio run --resume <run_id>`: every idea keeps
what it already has (angle, core caption, platform captions, design, spec, render), so no finished AI call is paid for
twice and no render is repeated. Stages an idea passes, in order: planned → angled → written → adapted → designed →
prepared → rendered → final.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

STAGES = ("planned", "angled", "written", "adapted", "designed", "prepared", "rendered", "final")


class RunState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.data: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {"ideas": {}, "dropped": []}

    @classmethod
    def exists(cls, path: Path) -> bool:
        return path.exists()

    def set(self, **top: Any) -> None:
        with self._lock:
            self.data.update(top)
            self._save()

    def add_idea(self, idea_id: str, **fields: Any) -> None:
        with self._lock:
            self.data["ideas"].setdefault(idea_id, {"stage": "planned", "holds": {}}).update(fields)
            self._save()

    def update(self, idea_id: str, stage: str | None = None, **fields: Any) -> None:
        with self._lock:
            row = self.data["ideas"][idea_id]
            row.update(fields)
            if stage:
                assert stage in STAGES, stage
                row["stage"] = stage
            self._save()

    def hold(self, idea_id: str, why: str, platform: str | None = None, code: str = "held") -> None:
        """Something that did not go out, and why: for the whole idea, or one platform of it."""
        with self._lock:
            row = self.data["ideas"][idea_id]
            row["holds"][platform or "*"] = {"code": code, "why": why}
            if platform is None:
                row["stage"] = "final"
            self._save()

    def idea(self, idea_id: str) -> dict[str, Any]:
        with self._lock:
            row: dict[str, Any] = self.data["ideas"][idea_id]
            return row

    def reached(self, idea_id: str, stage: str) -> bool:
        return STAGES.index(self.idea(idea_id)["stage"]) >= STAGES.index(stage)

    def held_whole(self, idea_id: str) -> bool:
        return "*" in self.idea(idea_id)["holds"]

    def _save(self) -> None:
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, default=str))
        os.replace(tmp, self.path)
