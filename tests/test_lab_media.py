"""lab/agentic-slice/media.py: what code decides around the designer (job ids, the envelope of a spec, footage
variety, the reels CLI contract). Pure logic and one fake `npm`; no model, no render."""

import datetime as dt
import importlib.util
import json
import re
import stat
import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[1] / "lab" / "agentic-slice"


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("lab_media", LAB / "media.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lab_media"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()
AT = "2026-10-13T17:00:00+02:00"  # a Tuesday in ISO week 42
VIDEO = {"kind": "video", "hook": "Sealed.", "shots": [{"category": "Eating"}, {"category": "Eating"}]}
SLIDES = {"kind": "slides", "size": "album", "slides": [{"template": "photo", "title": "a"}, {"template": "card"}]}


def finalize(content, fmt="reel", **over):  # type: ignore[no-untyped-def]
    answer = {"spec": {"content": content, **over}}
    return M.finalize_spec(
        answer,
        jid="2026w42-ig-tue1700-s1",
        platform="instagram",
        slot_format=fmt,
        planned_posted_at=AT,
        caption="Kacchi in Berlin.",
        avoid=["c0001"],
        week_label="2026w42",
    )


def test_a_job_id_is_valid_for_both_systems_and_stable():
    jid = M.job_id("instagram", "s1", AT)
    assert jid == "2026w42-ig-tue1700-s1" and jid == M.job_id("instagram", "s1", AT)
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,80}", jid)  # reels' job_id rule
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", jid)  # the publisher's post_key rule
    assert M.job_id("instagram", "s1", AT, "R2") == "2026w42-ig-tue1700-s1-r2"
    assert M.job_id("facebook", "Slot 9!", AT) == "2026w42-fb-tue1700-slot9"


def test_kinds_follow_the_slot_format():
    assert [M.kind_for(f) for f in ("reel", "video", "short", "carousel", "image", "anything")] == [
        "video", "video", "video", "carousel", "image", "image"]  # fmt: skip


def test_the_envelope_of_a_spec_is_set_by_code_not_by_the_designer():
    hijack = {"platforms": ["tiktok"], "at": "2020-01-01T00:00:00Z"}
    spec, why = finalize(
        VIDEO, captions={"instagram": "evil"}, publish=hijack, job_id="other", avoid_assets=["x"], campaign="hijack"
    )
    assert why == "" and spec
    assert spec["job_id"] == "2026w42-ig-tue1700-s1" and spec["campaign"] == "2026w42"
    assert spec["captions"] == {"instagram": "Kacchi in Berlin."}
    assert spec["publish"] == {"platforms": ["instagram"], "at": AT}
    assert spec["avoid_assets"] == ["c0001"] and spec["language"] == ["en", "de"]
    assert spec["content"]["formats"] == ["9:16"] and spec["content"]["template"] == "beat-reel"


def test_slides_are_sized_for_what_the_platform_takes():
    carousel, _ = finalize({**SLIDES, "size": "story"}, fmt="carousel")
    assert carousel and carousel["content"]["size"] == "carousel" and len(carousel["content"]["slides"]) == 2
    image, _ = finalize(SLIDES, fmt="image")
    assert image and image["content"]["size"] == "album" and len(image["content"]["slides"]) == 1  # one picture


@pytest.mark.parametrize(
    ("content", "fmt", "snippet"),
    [(SLIDES, "reel", "needs a video spec"), (VIDEO, "carousel", "needs a slides spec"),
     ({"kind": "slides", "slides": []}, "image", "needs a slides spec")],
)  # fmt: skip
def test_a_design_of_the_wrong_kind_is_refused_with_why(content, fmt, snippet):
    spec, why = finalize(content, fmt=fmt)
    assert spec is None and snippet in why


@pytest.mark.parametrize("answer", [None, {}, {"spec": None}, {"spec": {}}, {"spec": {"content": "x"}}, "text"])
def test_an_answer_that_is_not_a_spec_is_refused_not_trusted(answer):
    spec, why = M.finalize_spec(answer, jid="x", platform="instagram", slot_format="reel", planned_posted_at=AT,
                                caption="c", avoid=[], week_label="w")  # fmt: skip
    assert spec is None and why


def test_the_designers_language_is_kept_if_valid_so_the_review_gate_can_see_it():
    spec, _ = finalize(VIDEO, language=["ur", "en"])
    assert spec and spec["language"] == ["ur", "en"]
    spec, _ = finalize(VIDEO, language="ur")
    assert spec and spec["language"] == ["en", "de"]


def test_the_footage_ledger_remembers_the_last_jobs_and_replaces_a_redo(tmp_path):
    ledger = M.AssetLedger(tmp_path / "l.json")
    now = dt.datetime(2026, 10, 12, tzinfo=dt.UTC)
    for i in range(14):
        ledger.record(f"job-{i}", [f"a{i}", "shared"], now)
    recent = M.AssetLedger(tmp_path / "l.json").recent()  # reloaded from disk
    assert "a13" in recent and "a2" in recent and "a1" not in recent and recent.count("shared") == 1
    ledger.record("job-13", ["b13"], now)
    assert "b13" in ledger.recent() and "a13" not in ledger.recent()


def test_the_ledger_records_clips_not_just_moments():
    manifest = {"plan": {"shots": [{"asset": "v-c0167-0168", "src": "footage/k/c0167.mp4"},
                                   {"asset": "v-c0167-0200", "src": "footage/k/c0167.mp4"}]}}  # fmt: skip
    assert M.assets_used(manifest) == ["v-c0167-0168", "c0167", "v-c0167-0200"]
    assert M.assets_used({"plan": {"slides": [{"asset": "p-1"}]}}) == ["p-1"]
    assert M.assets_used({}) == []


def test_the_inventory_is_compact():
    inv = {"video": {"Pot opening": {"n": 12, "star1": 4, "star2": 3}, "Rice": {"n": 5, "star1": 2, "star2": 0},
                     "Eating": {"n": 1, "star1": 0, "star2": 0}}}  # fmt: skip
    assert M.compact_inventory(inv)["video"] == {
        "Eating": "1",
        "Pot opening": "12, 3 key moments",
        "Rice": "5, 2 best takes",
    }


def _fake_npm(tmp_path: Path, script: str) -> dict[str, str]:
    bin_ = tmp_path / "bin"
    bin_.mkdir(parents=True)
    npm = bin_ / "npm"
    npm.write_text(f"#!/bin/sh\n{script}\n")
    npm.chmod(npm.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{bin_}:/usr/bin:/bin"}


def test_the_reels_cli_contract_exit_code_and_last_json_line(tmp_path, monkeypatch):
    root = tmp_path / "reels"
    root.mkdir()
    (root / "package.json").write_text("{}")
    monkeypatch.setenv("PATH", _fake_npm(tmp_path, 'echo "warming up"; echo \'{"ok":true,"status":"ready"}\'')["PATH"])
    assert M.reels_job(tmp_path / "s.json", reels=root) == (0, {"ok": True, "status": "ready"})
    monkeypatch.setenv(
        "PATH", _fake_npm(tmp_path / "b", 'echo \'{"ok":false,"error":"invalid spec"}\'; exit 2')["PATH"]
    )
    code, out = M.reels_job(tmp_path / "s.json", reels=root)
    assert code == 2 and out["error"] == "invalid spec"
    monkeypatch.setenv("PATH", _fake_npm(tmp_path / "c", "echo boom >&2; exit 1")["PATH"])
    code, out = M.reels_job(tmp_path / "s.json", reels=root)
    assert code == 3 and "boom" in out["error"]  # a crash with no summary is a render error with the reason


def test_a_missing_reels_engine_is_a_clear_error(tmp_path):
    code, out = M.reels_job(tmp_path / "s.json", reels=tmp_path / "nowhere")
    assert code == 3 and "DK_REELS_DIR" in out["error"]


def test_the_designer_task_carries_everything_so_it_spends_no_turns_looking_things_up():
    vocab = {"music_cultures": ["desi"], "music_moods": ["happy"], "templates": {"slides": {"photo": "x", "card": "y"}}}
    inv = {"video": {"Eating": "3"}, "photo": {"Dum pot": "2"}}
    t = M.designer_task(jid="j", platform="instagram", slot_format="carousel", planned_posted_at=AT, angle="rice",
                        caption="c", evidence=[], brand=["b"], vocab=vocab, inventory=inv)  # fmt: skip
    assert (
        t["make"] == "carousel"
        and t["footage_categories"] == {"Dum pot": "2"}
        and t["slide_templates"] == ["photo", "card"]
    )
    v = M.designer_task(jid="j", platform="instagram", slot_format="reel", planned_posted_at=AT, angle="rice",
                        caption="c", evidence=[], brand=[], vocab=vocab, inventory=inv)  # fmt: skip
    assert v["make"] == "video" and v["footage_categories"] == {"Eating": "3"} and v["slide_templates"] == []
    json.dumps(t)  # it travels as JSON
