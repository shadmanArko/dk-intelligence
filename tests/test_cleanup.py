"""Freeing disk space (ADR 0042): what is deleted, and above all what never is."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from intel.studio import cleanup

NOW = dt.datetime(2026, 10, 13, 18, 0, tzinfo=dt.UTC)
CFG = cleanup.RetentionCfg()  # keep 3 days after a post, 1 day for what no post knows, 3 packs, 1 upload copy


def _folder(root: Path, name: str, age_days: float, size: int = 100) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "post.mp4").write_bytes(b"x" * size)
    when = (NOW - dt.timedelta(days=age_days)).timestamp()
    os.utime(d, (when, when))
    return d


def _row(key: str, state: str, slot: str) -> dict[str, Any]:
    return {"post_key": key, "state": state, "slot": slot, "platform": "instagram", "source": "api"}


def test_a_video_stays_while_its_post_waits_or_was_posted_recently_and_goes_when_nothing_needs_it(tmp_path):
    jobs = tmp_path / "out" / "jobs"
    for name in ("waiting", "scheduled-late", "posted-today", "posted-old", "cancelled-old", "experiment-old",
                 "experiment-new", "resumable"):  # fmt: skip
        _folder(jobs, name, age_days=0.2 if name in ("experiment-new",) else 6)
    rows = [_row("waiting", "awaiting_approval", "2026-10-20T20:00:00+02:00"),
            _row("scheduled-late", "scheduled", "2026-10-05T20:00:00+02:00"),  # overdue, but still not final
            _row("posted-today", "published", "2026-10-13T12:00:00+02:00"),
            _row("posted-old", "published", "2026-10-02T12:00:00+02:00"),
            _row("cancelled-old", "cancelled", "2026-10-02T12:00:00+02:00")]  # fmt: skip
    plan = cleanup.plan_jobs(jobs, cleanup.by_post(rows), {"resumable"}, NOW, CFG)
    kept = {i.path.name: i.why for i in plan.keep}
    gone = {i.path.name: i.why for i in plan.delete}
    assert set(kept) == {"waiting", "scheduled-late", "posted-today", "experiment-new", "resumable"}
    assert set(gone) == {"posted-old", "cancelled-old", "experiment-old"}
    assert (
        "still waits" in kept["scheduled-late"]
        and "resumed" in kept["resumable"]
        and "not handed over" in kept["experiment-new"]
    )
    assert plan.freed == 300


def test_one_variant_that_still_needs_its_files_keeps_the_post_and_the_aggressive_setting_deletes_new_experiments(
    tmp_path,
):
    jobs = tmp_path / "jobs"
    _folder(jobs, "idea", age_days=5)
    _folder(jobs, "fresh-test", age_days=0.1)
    rows = [
        _row("idea", "published", "2026-10-01T12:00:00+02:00"),
        _row("idea", "scheduled", "2026-10-14T12:00:00+02:00"),
    ]
    posts = cleanup.by_post(rows)
    assert posts["idea"]["live"] is True  # one platform still to go: the files stay
    plan = cleanup.plan_jobs(jobs, posts, set(), NOW, CFG.model_copy(update={"unknown_keep_days": 0}))
    assert [i.path.name for i in plan.keep] == ["idea"] and [i.path.name for i in plan.delete] == ["fresh-test"]


def test_only_runs_that_can_still_be_resumed_protect_their_videos(tmp_path):
    runs = tmp_path / "runs"

    def run(name: str, last: dict[str, Any] | None, folder: str) -> None:
        d = runs / name
        d.mkdir(parents=True)
        events = [{"node": "orchestrator", "kind": "start"}] + ([last] if last else [])
        (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        (d / "state.json").write_text(json.dumps({"ideas": {"i": {"spec_path": f"/x/out/jobs/{folder}/spec.json"}}}))

    run("cut", None, "from-cut-off-run")
    run("stopped", {"node": "orchestrator", "kind": "end", "status": "stopped"}, "from-stopped-run")
    run("failed", {"node": "orchestrator", "kind": "end", "status": "failed"}, "from-failed-run")
    run("done", {"node": "orchestrator", "kind": "end", "status": "done"}, "from-finished-run")
    assert cleanup.protected_by_runs(runs, dt.datetime.now(dt.UTC)) == {
        "from-cut-off-run",
        "from-stopped-run",
        "from-failed-run",
    }


def test_nothing_is_deleted_when_the_publisher_cannot_be_reached_or_a_run_is_working(tmp_path):
    reels = tmp_path / "reels"
    _folder(reels / "out" / "jobs", "anything", age_days=9)

    class Down:
        def calendar(self, **f: Any) -> dict[str, Any]:
            raise OSError("unreachable")

    data = tmp_path / "data" / "studio"
    data.mkdir(parents=True)
    r = cleanup.run(CFG, Down(), reels, data, NOW, apply=True)
    assert r.jobs.skipped and "cannot be reached" in r.jobs.skipped and (reels / "out" / "jobs" / "anything").exists()

    class Up:
        def calendar(self, **f: Any) -> dict[str, Any]:
            return {"posts": []}

    r = cleanup.run(CFG, Up(), reels, data, NOW, apply=True, working=True)
    assert "working" in (r.jobs.skipped or "") and (reels / "out" / "jobs" / "anything").exists()
    r = cleanup.run(CFG, Up(), reels, data, NOW, apply=True)
    assert not (reels / "out" / "jobs" / "anything").exists() and r.removed == 1 and r.freed == 100


def test_old_knowledge_packs_go_but_the_active_one_and_the_newest_stay_and_uploads_keep_one_copy(tmp_path):
    packs = tmp_path / "knowledge"
    for i in range(1, 8):
        _folder(packs, f"kp-2026100{i}T100000-aaaaaa", age_days=1)
    (packs / "ACTIVE").write_text("kp-20261001T100000-aaaaaa\n")  # an old pack that is nevertheless the active one
    (packs / "cache").mkdir()
    plan = cleanup.plan_packs(packs, keep=3)
    assert {i.path.name[3:12] for i in plan.keep} == {"20261001T", "20261005T", "20261006T", "20261007T"}
    assert len(plan.delete) == 3 and (packs / "cache").exists()  # only kp-* folders are ever candidates
    up = tmp_path / "packages" / "knowledge"
    up.mkdir(parents=True)
    for n in (
        "knowledge-models-abc.tar.gz",
        "knowledge-models-abc.tar.gz.sha256",
        "knowledge-pack-kp-2.tar.gz",
        "knowledge-pack-kp-2.tar.gz.sha256",
        "knowledge-pack-kp-1.tar.gz",
        "knowledge-pack-kp-1.tar.gz.sha256",
    ):
        (up / n).write_bytes(b"x" * 10)
    plan = cleanup.plan_uploads(up, keep=1)
    assert {i.path.name for i in plan.keep} == {"knowledge-pack-kp-2.tar.gz", "knowledge-pack-kp-2.tar.gz.sha256"}
    assert len(plan.delete) == 4  # the checksum files travel with their copy


def test_the_delete_function_refuses_anything_outside_the_folders_it_was_given_and_symlinks(tmp_path):
    root = tmp_path / "out" / "jobs"
    inside = _folder(root, "post", 5)
    outside = _folder(tmp_path / "footage", "clip", 5)  # the owner's own footage must never be reachable
    link = root / "link"
    link.symlink_to(outside)
    assert not cleanup.delete(cleanup.Item(outside, 1, "x"), (root,)) and outside.exists()
    assert not cleanup.delete(cleanup.Item(link, 1, "x"), (root,)) and outside.exists()
    assert not cleanup.delete(cleanup.Item(root.parent, 1, "x"), (root,)) and root.exists()
    assert cleanup.delete(cleanup.Item(inside, 1, "x"), (root,)) and not inside.exists()
    assert cleanup.human(1_500_000_000) == "1.5 GB" and cleanup.human(2_000_000) == "2 MB"


def test_the_daily_check_tidies_once_a_day_never_while_a_run_works_and_tells_the_owner_only_when_it_matters(
    tmp_path, monkeypatch
):
    from intel.studio import cli

    calls: list[bool] = []

    def fake_run(
        cfg: Any, client: Any, reels: Path, data: Path, now: dt.datetime, apply: bool = False, **kw: Any
    ) -> Any:
        calls.append(apply)
        return cleanup.Result(
            cleanup.Plan(), cleanup.Plan(), cleanup.Plan(), applied=True, freed=500_000_000, removed=7
        )

    notes: list[tuple[str, str]] = []

    class Rec:
        def __init__(self, folder: Path) -> None: ...

        def notify(self, title: str, message: str) -> None:
            notes.append((title, message))

    monkeypatch.setattr(cli, "DATA", tmp_path)
    monkeypatch.setattr(cli.cleanup_mod, "run", fake_run)
    monkeypatch.setattr(cli, "LocalNotifier", Rec)
    studio = type("S", (), {"publisher": object()})()
    cli._auto_cleanup(studio)  # noqa: SLF001
    assert calls == [True] and notes and "500 MB" in notes[0][1] and (tmp_path / "last-cleanup").exists()
    cli._auto_cleanup(studio)  # noqa: SLF001 - the same day: nothing
    assert calls == [True]
    (tmp_path / "last-cleanup").unlink()
    (tmp_path / ".running").write_text(f"{os.getpid()} 20261013T100000Z")  # a live run
    cli._auto_cleanup(studio)  # noqa: SLF001
    assert calls == [True]  # not while a run works


def test_the_page_previews_the_cleanup_without_deleting_and_today_warns_when_the_disk_is_nearly_full(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from intel.studio import console, console_system, today
    from tests.test_studio import _readers

    reels = tmp_path / "reels"
    _folder(reels / "out" / "jobs", "old-test", age_days=9, size=1000)
    monkeypatch.setattr(console_system, "DATA", tmp_path / "data")
    monkeypatch.setattr(cleanup.shutil, "rmtree", lambda *a, **k: 1 / 0)  # a preview must never get as far as deleting
    from intel.studio import config as sc
    from intel.studio import media

    monkeypatch.setattr(media, "REELS", reels)
    c = TestClient(console.app, base_url="http://127.0.0.1:8771", headers={"x-intel-token": console.TOKEN})
    out = c.get("/api/cleanup").json()
    assert set(out) == {"disk", "videos", "packs", "uploads", "would_free"} and out["disk"]["free_percent"] > 0
    assert (reels / "out" / "jobs" / "old-test").exists()  # asking never deletes
    cfg = sc.load()
    full = today.build(_readers(disk_free=lambda: 0.08), cfg.schedule, [])
    item = next(i for i in full["items"] if i["id"] == "disk")
    assert item["level"] == "todo" and item["actions"][0]["body"] == {"action": "cleanup", "confirm": True}
    roomy = today.build(_readers(disk_free=lambda: 0.5), cfg.schedule, [])
    assert "disk" not in {i["id"] for i in roomy["items"]}
