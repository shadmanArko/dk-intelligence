"""The MCP audit chain: one JSON line per call, read or get, each hashed over the previous line's hash.

Several MCP processes (one per Claude Code session) append to the same file, so every append holds an exclusive
flock and fsyncs. Arguments are stored as a hash, never in clear: they may contain drafts or queries. `verify`
recomputes the chain; any edited, removed or reordered line breaks it from that point on.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
CHAIN = "chain.jsonl"


def _digest(prev: str, entry: dict[str, Any]) -> str:
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev + body).encode()).hexdigest()


def args_hash(arguments: Any) -> str:
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()[:16]


class AuditLog:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.path = directory / CHAIN

    def append(self, **entry: Any) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {"at": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"), **entry}
        with self.path.open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                prev = _last_hash(fh)
                record["prev"] = prev
                record["hash"] = _digest(prev, {k: v for k, v in record.items() if k != "hash"})
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        return str(record["hash"])

    def entries(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open() as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)

    def verify(self) -> dict[str, Any]:
        prev, n = GENESIS, 0
        for n, e in enumerate(self.entries(), start=1):
            expected = _digest(prev, {k: v for k, v in e.items() if k != "hash"})
            if e.get("prev") != prev or e.get("hash") != expected:
                return {"ok": False, "entries": n, "first_bad": n}
            prev = e["hash"]
        return {"ok": True, "entries": n, "first_bad": None}


def _last_hash(fh: Any) -> str:
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    if size == 0:
        return GENESIS
    back = min(size, 8192)
    while True:
        fh.seek(size - back)
        tail = fh.read().splitlines()
        lines = [ln for ln in tail if ln.strip()]
        if len(lines) >= 2 or back == size:
            return str(json.loads(lines[-1])["hash"]) if lines else GENESIS
        back = min(size, back * 4)
