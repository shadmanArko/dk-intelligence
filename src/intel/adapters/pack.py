"""PackBrandSource and PackTrendSource: parts of the active knowledge pack, checksum-verified on every change of
pack."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

from intel.capabilities.ports import BrandPack
from intel.capabilities.types import ToolProblem
from intel.rag.pack import TRENDS, PackError, active_id, verify


class PackBrandSource:
    """Fail closed: a missing or tampered pack makes every brand tool answer BRAND_UNAVAILABLE."""

    def __init__(self, pack_root: Path) -> None:
        self.root = pack_root
        self._lock = threading.Lock()
        self._cached: BrandPack | None = None

    def load(self) -> BrandPack:
        pack_id = active_id(self.root)
        if pack_id is None:
            raise ToolProblem("BRAND_UNAVAILABLE", "no active knowledge pack", "the owner runs `intel pack build`")
        with self._lock:
            if self._cached is not None and self._cached.pack_id == pack_id:
                return self._cached
            try:
                manifest = verify(self.root / pack_id)
                brand = self.root / pack_id / "brand"
                pack = BrandPack(
                    pack_id=pack_id,
                    playbook=_yaml(brand / "playbook.yaml"),
                    content_rules=_yaml(brand / "content_rules.yaml"),
                    facts=_yaml(brand / "facts.yaml"),
                    documents={
                        "brand-book.md": (brand / "brand-book.md").read_text(),
                        "voice-and-rules.md": (brand / "voice-and-rules.md").read_text(),
                        "facts.yaml": (brand / "facts.yaml").read_text(),
                    },
                    sources=[{"file": k, "sha256": v} for k, v in sorted(manifest["files"].items())],
                )
            except (PackError, OSError, yaml.YAMLError) as e:
                raise ToolProblem("BRAND_UNAVAILABLE", f"the brand pack can't be read: {e}") from e
            self._cached = pack
            return pack


class PackTrendSource:
    """The pack's trends table (market posts' engagement lift by tag). Fails closed like the brand source."""

    def __init__(self, pack_root: Path) -> None:
        self.root = pack_root
        self._lock = threading.Lock()
        self._cached: tuple[str, list[dict[str, Any]]] | None = None

    def load(self) -> tuple[str, list[dict[str, Any]]]:
        import pandas as pd

        pack_id = active_id(self.root)
        if pack_id is None:
            raise ToolProblem("UPSTREAM_UNAVAILABLE", "no active knowledge pack", "the owner runs `intel pack build`")
        with self._lock:
            if self._cached is not None and self._cached[0] == pack_id:
                return self._cached
            try:
                manifest = verify(self.root / pack_id)
                if TRENDS not in manifest["files"]:
                    raise PackError(f"{pack_id} has no trends table (built before P4.3)")
                rows = pd.read_parquet(self.root / pack_id / TRENDS).to_dict("records")
            except (PackError, OSError) as e:
                raise ToolProblem(
                    "UPSTREAM_UNAVAILABLE", f"trends can't be read: {e}", "uv run intel pack build"
                ) from e
            self._cached = (pack_id, [{str(k): v for k, v in r.items()} for r in rows])
            return self._cached


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise PackError(f"{path.name} is not a mapping")
    return data
