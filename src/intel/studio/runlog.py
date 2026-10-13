"""The run log: one JSON line per event in `events.jsonl` of the run folder (A17). The console draws from it."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path
from typing import Any


def short(value: Any, n: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= n else text[: n - 1] + "…"


class RunLog:
    def __init__(self, directory: Path, run_id: str) -> None:
        self.id = run_id
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = directory / "events.jsonl"
        self._lock = threading.Lock()
        self._t0 = time.monotonic()

    def emit(self, node: str, kind: str, **data: Any) -> None:
        if {"t", "ms", "node", "kind"} & data.keys():
            raise ValueError("event data must not overwrite the timeline fields")
        with self._lock, self.path.open("a") as f:  # stamped under the lock: the file is in time order
            event = {"t": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
                     "ms": round((time.monotonic() - self._t0) * 1000), "node": node, "kind": kind, **data}  # fmt: skip
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def phase(self, name: str, status: str, **data: Any) -> None:
        """A named stage of the run (planning, writing, checking, rendering, handover) starting or ending."""
        self.emit("orchestrator", "phase", phase=name, status=status, **data)


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
