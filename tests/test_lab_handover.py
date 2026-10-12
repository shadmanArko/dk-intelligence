"""lab/agentic-slice/handover.py: manifest -> drafts in dk-publishing. A fake publisher (httpx.MockTransport) enforces
the same rules the real contract states (format and file-count limits, required options, idempotent post_key, media
that can expire), so what is tested is the bridge's behaviour: drafts only, idempotent, nothing dropped silently, the
key never leaking. No network."""

import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

LAB = Path(__file__).resolve().parents[1] / "lab" / "agentic-slice"
KEY = "dkp_SECRETSECRETSECRET"
NOW = dt.datetime(2026, 10, 12, 18, 0, tzinfo=dt.UTC)
SLOT = "2026-10-14T16:30:00+02:00"


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("lab_handover", LAB / "handover.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lab_handover"] = mod  # dataclasses look their module up here while the class is built
    spec.loader.exec_module(mod)
    return mod


H = _load()


def _fmt(lo: int, hi: int, caption: int | None = 2200, options: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "media": {"min": lo, "max": hi, "types": ["image/jpeg"]},
        "caption": {"kind": "text", "required": False, "max_length": caption} if caption else None,
        "options": options or {},
    }


SPEC: dict[str, Any] = {
    "spec": "publishing-spec/v1",
    "spec_version": "abc123",
    "post": {
        "post_key_pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$",
        "slot": {"min_lead_s": 60, "max_lead_s": 366 * 86400},
    },
    "platforms": {
        "instagram": {
            "mode": "live",
            "formats": {"reel": _fmt(1, 1), "carousel": _fmt(2, 10), "feed": _fmt(1, 1), "story": _fmt(1, 1, None)},
        },
        "facebook": {"mode": "live", "formats": {"video": _fmt(1, 1, 500), "photo": _fmt(1, 10, 500)}},
        "threads": {"mode": "live", "formats": {"video": _fmt(1, 1, 500), "image": _fmt(1, 1, 500)}},
        "youtube": {
            "mode": "live",
            "formats": {
                "video": _fmt(
                    1,
                    1,
                    None,
                    {
                        "title": {"kind": "text", "required": True, "max_length": 100},
                        "made_for_kids": {"kind": "choice", "required": True, "choices": ["yes", "no"]},
                    },
                )
            },
        },
        "tiktok": {
            "mode": "assisted",
            "formats": {
                "video": _fmt(
                    1,
                    1,
                    2200,
                    {
                        "privacy_level": {"kind": "choice", "required": True, "choices": ["public", "only_me"]},
                    },
                )
            },
        },
    },
}
CFG = {
    "reviewed_languages": [],
    "defaults": {"tiktok": {"privacy_level": "only_me"}, "youtube": {"made_for_kids": "no"}},
}


class FakePublisher:
    """Just enough of dk-publishing to test the bridge against: upload, idempotent drafts, validation."""

    def __init__(self) -> None:
        self.media: dict[str, bytes] = {}
        self.posts: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_next: list[int] = []  # statuses to answer with before behaving
        self.expire_all_media_once = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        path = request.url.path
        self.calls.append((request.method, path))
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), json={"title": "try later"})
        if path == "/v1/spec":
            if request.headers.get("If-None-Match") == '"abc123"':
                return httpx.Response(304)
            return httpx.Response(200, json=SPEC)
        if path == "/v1/media":
            media_id = "m-" + hashlib.sha256(request.content).hexdigest()[:10]
            self.media[media_id] = request.content
            return httpx.Response(201, json={"media_id": media_id})
        if path == "/v1/posts/batch":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={"dry_run": body["dry_run"], "results": [self._one(p, body["dry_run"]) for p in body["posts"]]},
            )
        if path.startswith("/v1/posts/") and request.method == "GET":
            key = path.rsplit("/", 1)[1]
            return (
                httpx.Response(200, json={"post_key": key, "state": "awaiting_approval"})
                if key in self.posts
                else httpx.Response(404, json={"title": "no such post"})
            )
        if path == "/v1/overview":
            return httpx.Response(
                200,
                json={
                    "pipeline": {"total": 2},
                    "published": {"total": 5},
                    "problems": [],
                    "awaiting_approval": [{}, {}],
                    "plan_ends": "2026-10-15",
                    "note": "n",
                },
            )
        return httpx.Response(404, json={"title": "no"})

    def _one(self, post: dict[str, Any], dry: bool) -> dict[str, Any]:
        problems = []
        for i, v in enumerate(post["variants"]):
            for m in v["media"]:
                if m not in self.media or self.expire_all_media_once:
                    problems.append({"path": f"variants[{i}].media", "code": "media_expired"})
        if problems:
            return {"post_key": post["post_key"], "ok": False, "problems": problems}
        if post["post_key"] in self.posts:
            if self.posts[post["post_key"]] != post:
                return {"post_key": post["post_key"], "ok": False, "problems": [{"code": "post_key_taken"}]}
            return {"post_key": post["post_key"], "ok": True, "replayed": True}
        if not dry:
            self.posts[post["post_key"]] = post
        return {"post_key": post["post_key"], "ok": True}


@pytest.fixture
def world(tmp_path):
    fake = FakePublisher()
    client = H.PublishClient(
        "https://publish.test", KEY, transport=httpx.MockTransport(fake.handler), sleep=lambda s: None
    )
    ledger = H.MediaLedger(tmp_path / "ledger.json")
    return fake, client, ledger, tmp_path


ENVELOPE = {"manifest_version": 1, "campaign": "wk42", "spec_hash": "h", "created_at": "2026-10-12T18:00:00Z",
            "review": []}  # fmt: skip
FILE = {"width": 1080, "height": 1920, "bytes": 1, "sha256": "a" * 64}


def reel(tmp_path: Path, job: str = "wk42-reel-1", **over: Any) -> tuple[dict[str, Any], Path]:
    folder = tmp_path / job
    folder.mkdir(exist_ok=True)
    (folder / f"{job}_9x16.mp4").write_bytes(b"video-" + job.encode())
    m: dict[str, Any] = {
        **ENVELOPE,  # the fields every real manifest has (the pinned contract, tests/test_contracts.py)
        "job_id": job,
        "status": "ready",
        "kind": "video",
        "template": "beat-reel",
        "language": ["en"],
        "qa": [],
        "files": [{"path": f"{job}_9x16.mp4", "kind": "video", "format": "9:16", **FILE}],
        "on_screen_text": [{"where": "hook", "text": "Seal broken, kacchi inside"}],
        "captions": {
            "instagram": "Kacchi biryani, Berlin.",
            "facebook": "Kacchi biryani, Berlin.",
            "tiktok": "Biryani.",
            "threads": "Biryani.",
        },
        "publish": {"platforms": ["instagram", "facebook", "youtube", "tiktok"], "at": SLOT},
    }
    m.update(over)
    return m, folder


def slides(
    tmp_path: Path,
    n: int,
    job: str = "wk42-carousel",
    size: str = "carousel",
    platforms: Any = ("instagram", "facebook", "threads"),
):
    folder = tmp_path / job
    folder.mkdir(exist_ok=True)
    names = [f"{job}-{i + 1:02d}.jpg" for i in range(n)]
    for x in names:
        (folder / x).write_bytes(b"img-" + x.encode())
    m = {
        **ENVELOPE,
        "job_id": job,
        "status": "ready",
        "kind": "slides",
        "template": f"slides:{size}",
        "language": ["en"],
        "qa": [],
        "on_screen_text": [],
        "files": [{"path": x, "kind": "image", "size": size, **FILE} for x in names],
        "captions": {"instagram": "Swipe.", "facebook": "Swipe.", "threads": "Swipe."},
        "publish": {"platforms": list(platforms), "at": SLOT},
    }
    return m, folder


def run(world, jobs, dry=False):  # type: ignore[no-untyped-def]
    fake, client, ledger, _ = world
    return fake, H.submit(client, jobs, SPEC, CFG, ledger, now=NOW, dry_run=dry)


# ---------- what goes out ----------


def test_a_reel_becomes_the_right_format_per_platform_with_required_options(world):
    fake, report = run(world, [reel(world[3])])
    assert report.submitted == ["wk42-reel-1"] and not report.held and not report.failed
    variants = {v["platform"]: v for v in fake.posts["wk42-reel-1"]["variants"]}
    assert {p: v["format"] for p, v in variants.items()} == {
        "instagram": "reel",
        "facebook": "video",
        "youtube": "video",
        "tiktok": "video",
    }
    assert variants["youtube"]["options"] == {"made_for_kids": "no", "title": "Seal broken, kacchi inside"}
    assert variants["tiktok"]["options"] == {"privacy_level": "only_me"}  # conservative default, owner can change it
    assert all(v["slot"] == SLOT and len(v["media"]) == 1 for v in variants.values())
    assert "caption" not in variants["youtube"]  # the format has no caption field


def test_a_dry_run_creates_nothing_and_a_real_run_dry_runs_first(world):
    fake, report = run(world, [reel(world[3])], dry=True)
    assert report.would_submit == ["wk42-reel-1"] and not fake.posts
    fake.calls.clear()
    run(world, [reel(world[3], "wk42-reel-2")])
    batches = [c for c in fake.calls if c[1] == "/v1/posts/batch"]
    assert len(batches) == 2  # a validating dry run, then the real one


def test_running_twice_replays_instead_of_duplicating_and_uploads_once(world):
    fake, first = run(world, [reel(world[3])])
    uploads = sum(1 for c in fake.calls if c[1] == "/v1/media")
    _, second = run(world, [reel(world[3])])
    assert first.submitted == ["wk42-reel-1"] and second.replayed == ["wk42-reel-1"] and not second.submitted
    assert len(fake.posts) == 1
    assert sum(1 for c in fake.calls if c[1] == "/v1/media") == uploads  # the ledger remembered the file


def test_one_slide_is_a_feed_post_several_are_a_carousel(world):
    fake, report = run(
        world,
        [
            slides(world[3], 1, "wk42-one", platforms=("instagram", "facebook", "threads")),
            slides(world[3], 3, "wk42-three", platforms=("instagram", "facebook", "threads")),
        ],
    )
    one = {v["platform"]: v["format"] for v in fake.posts["wk42-one"]["variants"]}
    three = {v["platform"]: v["format"] for v in fake.posts["wk42-three"]["variants"]}
    assert one == {"instagram": "feed", "facebook": "photo", "threads": "image"}
    assert three == {"instagram": "carousel", "facebook": "photo"}
    assert any(h.platform == "threads" and h.code == "media_count" for h in report.held)  # said, not dropped


# ---------- nothing is dropped silently ----------


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"status": "qa_failed", "qa": [{"check": "loudness", "ok": False}]}, "qa_failed"),
        ({"publish": {"platforms": ["instagram"], "at": "2026-10-14T16:30:00"}}, "bad_slot"),
        ({"publish": {"platforms": ["instagram"], "at": "2026-10-12T17:00:00+02:00"}}, "slot_window"),
        ({"publish": {"platforms": ["instagram"], "at": "2028-10-14T16:30:00+02:00"}}, "slot_window"),
        ({"publish": {"platforms": [], "at": SLOT}}, "no_platforms"),
        ({"job_id": "bad id!"}, "bad_post_key"),
    ],
)
def test_a_job_that_cannot_go_out_is_held_with_a_code_and_uploads_nothing(world, over, code):
    fake, report = run(world, [reel(world[3], **over)])
    assert [h.code for h in report.held] == [code] and not report.submitted and not fake.posts
    assert not any(c[1] == "/v1/media" for c in fake.calls)
    assert report.held[0].reason


def test_text_that_needs_a_native_speaker_is_held_until_the_language_is_reviewed(world):
    fake, client, ledger, tmp = world
    job = reel(tmp, status="needs_review", language=["ur", "en"])
    held = H.submit(client, [job], SPEC, CFG, ledger, now=NOW)
    assert held.held[0].code == "needs_review" and "ur" in held.held[0].reason and not fake.posts
    reviewed = H.submit(client, [job], SPEC, {**CFG, "reviewed_languages": ["ur"]}, ledger, now=NOW)
    assert reviewed.submitted == ["wk42-reel-1"]
    partly = reel(tmp, "wk42-reel-3", status="needs_review", language=["ur", "ar", "en"])
    still = H.submit(client, [partly], SPEC, {**CFG, "reviewed_languages": ["ur"]}, ledger, now=NOW)
    assert still.held[0].code == "needs_review" and "ar" in still.held[0].reason


def test_a_platform_that_is_off_or_a_caption_that_is_too_long_holds_only_that_platform(world):
    caps = {"instagram": "x" * 2300, "facebook": "ok", "tiktok": "ok"}
    job = reel(world[3], captions=caps, publish={"platforms": ["instagram", "facebook", "pinterest"], "at": SLOT})
    fake, report = run(world, [job])
    assert {(h.platform, h.code) for h in report.held} == {
        ("instagram", "caption_too_long"),
        ("pinterest", "platform_off"),
    }
    assert [v["platform"] for v in fake.posts["wk42-reel-1"]["variants"]] == ["facebook"]


def test_a_required_option_nothing_supplies_holds_that_platform(world):
    fake, client, ledger, tmp = world
    report = H.submit(client, [reel(tmp)], SPEC, {"reviewed_languages": [], "defaults": {}}, ledger, now=NOW)
    # YouTube's title comes from the hook, but made_for_kids and TikTok's privacy_level need an explicit answer
    assert {(h.platform, h.code) for h in report.held} == {("tiktok", "option_missing"), ("youtube", "option_missing")}
    assert {v["platform"] for v in fake.posts["wk42-reel-1"]["variants"]} == {"instagram", "facebook"}


def test_the_spec_can_override_an_option(world):
    job = reel(
        world[3], publish={"platforms": ["tiktok"], "at": SLOT, "options": {"tiktok": {"privacy_level": "public"}}}
    )
    fake, _ = run(world, [job])
    assert fake.posts["wk42-reel-1"]["variants"][0]["options"] == {"privacy_level": "public"}


# ---------- the API misbehaving ----------


def test_batches_are_chunked_at_twenty(world):
    jobs = [reel(world[3], f"wk42-bulk-{i:02d}") for i in range(23)]
    fake, report = run(world, jobs)
    assert len(report.submitted) == 23
    assert [c for c in fake.calls if c[1] == "/v1/posts/batch"].__len__() == 4  # 2 chunks x (dry run + real)


def test_transient_failures_are_retried_but_a_refusal_is_not(world):
    fake, client, _, _ = world
    fake.fail_next = [503, 429, 503]
    assert client.spec()["spec_version"] == "abc123"
    fake.fail_next = [422]
    with pytest.raises(H.PublishError) as e:
        client.spec()
    assert e.value.status == 422 and len(fake.calls) == 5  # 3 retried + 1 ok, then 1 refused without a retry


def test_an_upload_that_expired_on_the_server_is_forgotten_and_uploaded_again(world):
    fake, client, ledger, tmp = world
    job = reel(tmp)
    H.submit(client, [job], SPEC, CFG, ledger, now=NOW, dry_run=True)  # uploads and remembers
    fake.media.clear()  # the server aged them out
    report = H.submit(client, [job], SPEC, CFG, ledger, now=NOW)
    assert report.submitted == ["wk42-reel-1"]
    assert all(v["media"][0] in fake.media for v in fake.posts["wk42-reel-1"]["variants"])


def test_the_key_never_appears_in_an_error_or_a_report(world):
    fake, client, _, tmp = world
    fake.fail_next = [503, 503, 503, 503]
    with pytest.raises(H.PublishError) as e:
        client.overview()
    assert KEY not in str(e.value)
    bad = httpx.Client(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError(f"boom {KEY}"))),
        base_url="https://x",
    )
    c = H.PublishClient("https://x", KEY, transport=bad._transport, sleep=lambda s: None)
    with pytest.raises(H.PublishError) as e2:
        c.overview()
    assert KEY not in str(e2.value)
    assert KEY not in json.dumps(H.Report().as_dict())


def test_the_contract_is_cached_by_version_and_survives_an_outage(world, tmp_path):
    fake, client, _, _ = world
    cache = tmp_path / "spec.json"
    client.spec(cache)
    fake.calls.clear()
    client.spec(cache)  # sends If-None-Match -> 304 -> cached copy
    assert fake.calls == [("GET", "/v1/spec")]
    fake.fail_next = [503] * 4
    assert client.spec(cache)["spec_version"] == "abc123"  # unavailable: the last contract is still the best we know


def test_status_is_a_compact_summary(world):
    _, client, _, _ = world
    s = H.status(client)
    assert s == {
        "pipeline": {"total": 2},
        "published": 5,
        "problems": [],
        "awaiting_approval": 2,
        "plan_ends": "2026-10-15",
        "note": "n",
    }


def test_env_is_read_without_printing_or_reading_other_keys(tmp_path, monkeypatch):
    for k in ("DK_PUBLISH_URL", "DK_PUBLISH_KEY"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("OTHER_SECRET=nope\nDK_PUBLISH_URL=https://p.test\nDK_PUBLISH_KEY='k123'\n")
    assert H.load_env(env) == {"DK_PUBLISH_URL": "https://p.test", "DK_PUBLISH_KEY": "k123"}


def test_an_upload_is_streamed_and_a_retry_sends_the_whole_file_again(tmp_path):
    """A slow home line (224 KB/s measured) can't send a reel in one 60 s write; the upload streams it in chunks, and
    a retry after a temporary error re-reads the file from the start."""
    big = tmp_path / "reel.mp4"
    big.write_bytes(b"x" * (H.UPLOAD_CHUNK * 3 + 17))
    seen: list[tuple[int, str | None]] = []
    answers = [503, 201]

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        seen.append((len(body), request.headers.get("Content-Length")))
        status = answers.pop(0)
        return httpx.Response(status, json={"media_id": "m-1"} if status == 201 else {"title": "busy"})

    client = H.PublishClient("https://publish.example.test", KEY, transport=httpx.MockTransport(handler),
                             sleep=lambda _: None)  # fmt: skip
    assert client.upload(big) == "m-1"
    size = big.stat().st_size
    assert seen == [(size, str(size)), (size, str(size))]  # both attempts sent the complete file
