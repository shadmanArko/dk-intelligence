"""The performance source on disk: `data/performance/<pull id>/{playbook.json, agent_posts.parquet}` (LATEST names the
newest). Missing files are 'nothing built yet', never an error the model has to interpret."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FilePerformanceSource:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _folder(self) -> Path | None:
        marker = self.root / "LATEST"
        if not marker.exists():
            return None
        folder = self.root / marker.read_text().strip()
        return folder if folder.exists() else None

    def playbook(self) -> dict[str, Any] | None:
        folder = self._folder()
        if folder is None or not (folder / "playbook.json").exists():
            return None
        data: dict[str, Any] = json.loads((folder / "playbook.json").read_text())
        return data

    def agent_posts(self) -> list[dict[str, Any]]:
        import pandas as pd

        folder = self._folder()
        if folder is None or not (folder / "agent_posts.parquet").exists():
            return []
        frame = pd.read_parquet(folder / "agent_posts.parquet")
        rows: list[dict[str, Any]] = json.loads(frame.to_json(orient="records", date_format="iso"))
        return rows
