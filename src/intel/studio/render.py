"""Rendering: the RendererPort for this Mac (the reels CLI), and keeping the Mac awake while a long run works.

A render uses every core, so renders are strictly one at a time; the studio overlaps them with AI work and uploads
instead (a pipeline), never with each other. An identical spec is a cache hit inside the reels engine (0.5 s).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from intel.studio import media


class ReelsRenderer:
    def __init__(self, reels: Path, timeout_s: int) -> None:
        self.reels, self.timeout_s = reels, timeout_s

    def dry_run(self, spec_path: Path) -> tuple[int, dict[str, Any]]:
        return media.reels_job(spec_path, "--dry-run", reels=self.reels, timeout_s=self.timeout_s)

    def render(self, spec_path: Path) -> tuple[int, dict[str, Any]]:
        return media.reels_job(spec_path, reels=self.reels, timeout_s=self.timeout_s)

    def thumbnail(self, manifest: dict[str, Any], folder: Path, out: Path) -> bool:
        return media.thumbnail(manifest, folder, out)


@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
    """`caffeinate -dims -w <our pid>` while the block runs (macOS only; elsewhere nothing). It also ends by itself
    if this process dies, so a crashed run cannot leave the Mac awake."""
    proc: subprocess.Popen[bytes] | None = None
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        proc = subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())])  # noqa: S603, S607 - fixed arguments
    try:
        yield
    finally:
        if proc is not None:
            proc.terminate()
