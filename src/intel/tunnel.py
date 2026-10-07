"""SSH tunnel to the VPS warehouse (door 1).

The `intel` user on the VPS can do nothing but forward to 127.0.0.1:5432, and only from the Mac's Tailscale
address. If something already listens on the local port (a tunnel you opened by hand), it is reused and left
alone; otherwise we open one and close it when the block ends.
"""

from __future__ import annotations

import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class TunnelError(RuntimeError):
    pass


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def ssh_command(ssh_target: str, key: Path, local_port: int, remote_port: int = 5432) -> list[str]:
    return [
        "ssh",
        "-i",
        str(key),
        "-N",
        "-L",
        f"{local_port}:127.0.0.1:{remote_port}",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "IdentitiesOnly=yes",
        ssh_target,
    ]


@contextmanager
def warehouse_tunnel(ssh_target: str | None, key: Path | None, local_port: int, wait_s: float = 20.0) -> Iterator[bool]:
    """Yield True if we opened the tunnel, False if an existing listener was reused."""
    if port_open(local_port):
        yield False
        return
    if not ssh_target or not key:
        raise TunnelError("DK_WAREHOUSE_SSH and DK_WAREHOUSE_SSH_KEY must be set to open the tunnel")
    if not key.exists():
        raise TunnelError(f"SSH key not found: {key}")

    proc = subprocess.Popen(
        ssh_command(ssh_target, key, local_port),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + wait_s
        while not port_open(local_port):
            if proc.poll() is not None:
                err = (proc.stderr.read() if proc.stderr else "").strip()
                raise TunnelError(f"ssh exited with code {proc.returncode}: {err or 'no message'}")
            if time.monotonic() > deadline:
                raise TunnelError(f"tunnel did not come up within {wait_s:.0f}s")
            time.sleep(0.2)
        yield True
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
