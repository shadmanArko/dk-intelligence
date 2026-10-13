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
        self.dir = directory.resolve()  # an agent runs in another folder: its config path must be absolute
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = directory / "events.jsonl"
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self.current_phase: str | None = None  # what the orchestrator is doing now (code-level calls note it)
        self._base = (
            self._last_ms()
        )  # a resumed run goes on from where its clock stopped (the time it was off is not counted)

    def _last_ms(self) -> int:
        if not self.path.exists():
            return 0
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):  # a line cut off by a power loss: the next event starts on a fresh line
            with self.path.open("ab") as f:
                f.write(b"\n")
        for line in reversed(self.path.read_text().splitlines()):
            if line.strip():
                try:
                    return int(json.loads(line).get("ms") or 0)
                except ValueError:  # a line cut off by a crash: the one before it will do
                    continue
        return 0

    def emit(self, node: str, kind: str, **data: Any) -> None:
        if {"t", "ms", "node", "kind"} & data.keys():
            raise ValueError("event data must not overwrite the timeline fields")
        with self._lock, self.path.open("a") as f:  # stamped under the lock: the file is in time order
            ms = self._base + round((time.monotonic() - self._t0) * 1000)
            event = {"t": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"), "ms": ms, "node": node,
                     "kind": kind, **data}  # fmt: skip
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def phase(self, name: str, status: str, **data: Any) -> None:
        """A named stage of the run (planning, writing, checking, rendering, handover) starting or ending."""
        self.current_phase = name if status == "start" else None
        self.emit("orchestrator", "phase", phase=name, status=status, **data)


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:  # the last line can be half written when the Mac lost power: skip it, never crash
                continue
    return out
