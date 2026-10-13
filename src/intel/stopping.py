"""Stopping a long run cleanly (ADR 0041).

The console's Stop button sends SIGTERM to a run's main process. Without this, Python would die on the spot and leave
the run half written: no end in its log, a lock left behind, an ML run that the hourly catch-up would start again. With
it, SIGTERM becomes `Stopped`, raised in the main thread, which the run catches to write its own honest ending
("stopped by the owner") before it exits. `STOP` tells worker threads to stop recording failures: a worker whose agent
was killed by the stop must not mark its idea as failed.

Only the main thread can install a signal handler; in any other thread this does nothing.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Iterator
from types import FrameType


class Stopped(BaseException):  # not an Exception: no "except Exception" in a run may swallow a stop
    """The owner asked this run to stop."""


STOP = threading.Event()


def children_of(pid: int) -> list[int]:
    """The processes started by `pid`, and theirs, deepest first (so a parent is never ended before its children)."""
    out: list[int] = []
    found = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False).stdout.split()  # noqa: S603, S607
    for c in found:
        out += children_of(int(c))
        out.append(int(c))
    return out


def end_children(pid: int | None = None, send: Callable[[int, int], None] = os.kill) -> int:
    """SIGTERM every descendant of this process (agents, renderers, tunnels): a stopping run must not wait for them."""
    n = 0
    for child in children_of(pid or os.getpid()):
        with contextlib.suppress(OSError):
            send(child, signal.SIGTERM)
            n += 1
    return n


def _handler(signum: int, frame: FrameType | None) -> None:
    if STOP.is_set():  # a second request while shutting down: already on its way
        return
    STOP.set()
    end_children()  # the agents and renderers it is waiting for end now, so the run can write its ending at once
    raise Stopped()


@contextlib.contextmanager
def sigterm_stops_cleanly() -> Iterator[None]:
    """Inside the block SIGTERM raises `Stopped` in the main thread. Restores the old handler on the way out."""
    STOP.clear()
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old = signal.signal(signal.SIGTERM, _handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, old)
        STOP.clear()
