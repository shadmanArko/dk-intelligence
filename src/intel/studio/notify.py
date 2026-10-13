"""Telling the owner what a run did, without a secret on the Mac.

The Telegram bot token lives on the VPS only (CLAUDE.md rule 4). So a scheduled run on the Mac tells the owner by what
is local: a macOS notification and a summary file next to the run (`last-summary.md`). The owner's morning Telegram
digest, sent by dk-publishing, already says "Review today's posts" when agent drafts wait, so the drafts reach Telegram
through the publisher. A Mac-to-Telegram path would need a new endpoint on the VPS: an owner decision (ADR 0037).
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path
from typing import Protocol


class Notifier(Protocol):
    def notify(self, title: str, message: str) -> None: ...


class NullNotifier:
    def notify(self, title: str, message: str) -> None:
        return None


class LocalNotifier:
    """A macOS notification (osascript, nothing to configure) and a line appended to `notifications.log`."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def notify(self, title: str, message: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with (self.folder / "notifications.log").open("a") as f:
            f.write(f"{title}: {message}\n")
        if sys.platform == "darwin":
            script = f"display notification {_quote(message)} with title {_quote(title)}"
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):  # the log line above is the record
                subprocess.run(["osascript", "-e", script], check=False, timeout=10, capture_output=True)  # noqa: S603, S607


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"')[:200] + '"'
