"""The contracts between the Dhaka Kacchi projects (Phase 5 plan §6, ADR 0033).

`contracts/registry.yaml` names every link: its owner, where the owner publishes it, the version this repo speaks,
and a pinned, reviewed copy in `contracts/pinned/`. Consumer tests (tests/test_contracts.py) check what this repo
produces against the pinned copies, so a change in reels or dk-publishing fails a test here before any run. `sync`
fetches the owners' current versions: `check=True` only reports drift (exit 1 in the CLI); otherwise it refreshes the
pinned files, to be reviewed in the diff and committed with whatever code change the drift needs.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from intel.settings import repo_root

REGISTRY = repo_root() / "contracts" / "registry.yaml"
DEFAULT_PUBLISH_URL = "https://publish.dhakakacchi.com"


@dataclass(frozen=True)
class Drift:
    name: str
    status: str  # same | changed | new | unavailable | runtime
    detail: str = ""


def registry(path: Path = REGISTRY) -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text())["contracts"]
    return data


def pinned(name: str, path: Path = REGISTRY) -> dict[str, Any]:
    """A pinned contract document (a JSON Schema or an OpenAPI document)."""
    entry = registry(path)[name]
    doc: dict[str, Any] = json.loads((repo_root() / entry["pinned"]).read_text())
    return doc


def _canonical(doc: Any) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _reels_dir(env: dict[str, str]) -> Path:
    return Path(env.get("DK_REELS_DIR") or repo_root().parent / "dhaka-kacchi-reels")


def _http_get(url: str) -> Any:
    import httpx

    r = httpx.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def sync(
    *,
    check: bool,
    env: dict[str, str] | None = None,
    fetch: Callable[[str], Any] = _http_get,
    path: Path = REGISTRY,
) -> list[Drift]:
    """Compare every pinned contract with its owner's current one; refresh the pinned files unless `check`."""
    env = dict(os.environ) if env is None else env
    out: list[Drift] = []
    for name, entry in registry(path).items():
        source = entry.get("source") or {}
        if "pinned" not in entry:
            out.append(Drift(name, "runtime", f"checked when used ({entry.get('version')})"))
            continue
        try:
            if "repo" in source:
                doc = json.loads((_reels_dir(env) / source["file"]).read_text())
            else:
                base = (env.get("DK_PUBLISH_URL") or DEFAULT_PUBLISH_URL).rstrip("/")
                doc = fetch(base + source["url"])
        except Exception as ex:  # noqa: BLE001 - an unreachable owner is reported, never guessed
            out.append(Drift(name, "unavailable", f"{type(ex).__name__}: {str(ex)[:120]}"))
            continue
        target = repo_root() / entry["pinned"]
        current = _canonical(doc)
        if not target.exists():
            status = "new"
        elif target.read_text() == current:
            out.append(Drift(name, "same"))
            continue
        else:
            status = "changed"
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(current)
        out.append(Drift(name, status, "review the diff, run tests/test_contracts.py" if not check else ""))
    return out
