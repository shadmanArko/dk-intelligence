"""The footage curator: new and unlabelled media become knowledge the renderer and the creative director can use.

    code: ask the reels engine what waits (list_unindexed: facts + a contact sheet per item)
    AI:   one call per item: look at the sheet, write the moments (category, mood, shot type, where text can sit, ...)
    code: hand each answer back to the engine (propose_asset_entry), which checks it against the real vocabulary and
          writes ONLY a pending file. A person approves with `npm run curate` in the reels repo: the agent proposes.

The contract (reels JOBS.md, MCP profile `curator`): list_unindexed {scope, limit} → items[{id, kind, file, sheet, tech,
existing?}], vocabulary; propose_asset_entry {id, moments[]} → {ok} or {ok: false, problems[{path, message}]}.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from intel.studio.ai import AI
from intel.studio.ports import ToolPort

SERVER = "dk_reels_curator"


def curate(tools: ToolPort, ai: AI, scope: str = "new", limit: int = 5) -> dict[str, Any]:
    """Curate up to `limit` waiting items. Returns {proposed, refused, skipped, waiting, tokens}; every item the agent
    could not do is named with the reason."""
    waiting = tools.call(SERVER, "list_unindexed", {"scope": scope, "limit": limit})
    if isinstance(waiting, dict) and "error" in waiting:
        raise RuntimeError(f"list_unindexed: {waiting['error']}")
    proposed: list[str] = []
    refused: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for item in waiting.get("items", []):
        item_id = str(item["id"])
        sheet = item.get("sheet")
        if item.get("kind") not in ("video", "photo") or not sheet or not Path(sheet).exists():
            skipped.append(
                {
                    "id": item_id,
                    "why": "no contact sheet to look at (run `npm run ingest -- --backfill`)"
                    if item.get("kind") in ("video", "photo")
                    else "an agent cannot listen: music is analysed by code",
                }
            )
            continue
        task = {"id": item_id, "kind": item["kind"], "tech": item.get("tech", {}), "existing": item.get("existing", []),
                "vocabulary": waiting.get("vocabulary", {})}  # fmt: skip
        problems: list[dict[str, Any]] = []
        for attempt in (1, 2):  # the engine's own refusal text goes back once, so a wrong word is fixed, not lost
            if attempt == 2:
                task = {**task, "repair": {"problems": problems}}
            res = ai.call(
                f"curator:{item_id}" + ("-repair" if attempt == 2 else ""),
                "curator",
                task,
                None,
                lambda a: isinstance(a.get("moments"), list) and bool(a["moments"]),
                files={"sheet.jpg": Path(sheet)},
            )
            if res.answer is None:
                problems = [{"path": "", "message": res.error or "no answer"}]
                break
            out = tools.call(SERVER, "propose_asset_entry", {"id": item_id, "moments": res.answer["moments"]})
            if isinstance(out, dict) and out.get("ok"):
                proposed.append(item_id)
                problems = []
                break
            problems = list((out or {}).get("problems") or [{"path": "", "message": str((out or {}).get("error"))}])
        if problems:
            refused.append({"id": item_id, "problems": problems})
    return {"proposed": proposed, "refused": refused, "skipped": skipped, "waiting": waiting.get("total_waiting"),
            "enrichable": waiting.get("enrichable")}  # fmt: skip
