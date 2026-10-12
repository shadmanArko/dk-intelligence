"""Hand rendered jobs over to dk-publishing as DRAFT posts. Plain code, no AI, 0 tokens (playbook A1).

    manifest (dk_reels) ──► plan variants per platform ──► upload media ──► ONE batch (dry run first) ──► Calendar
                            checked against GET /v1/spec    POST /v1/media    POST /v1/posts/batch

What it guarantees (each is a test in tests/test_lab_handover.py):
- Drafts only: the batch endpoint always creates drafts and there is no approve call here. The owner ticks approve.
- Idempotent: `post_key` = the job id, so running it again replays instead of duplicating; uploaded files are
  remembered by content hash so a re-run doesn't upload twice.
- Nothing is dropped silently: a post or platform that can't go out is returned as `held` with a reason the owner
  can read.
- Checked against the live contract (`GET /v1/spec`: formats, file counts, caption limits, required options, slot
  window) before anything is sent, so the Calendar never fills with posts that fail validation.
- A post whose on-screen text needs a native-speaker check (non-Latin scripts) is held until its language is listed in
  `handover.json` → `reviewed_languages`.
- The key (`DK_PUBLISH_KEY`, right: draft) is read from the environment or `.env`, sent only as a Bearer header, and
  never printed, logged or put in an error message.

    uv run python lab/agentic-slice/handover.py --manifest <path/to/manifest.json> [--manifest ...] [--dry-run]
    uv run python lab/agentic-slice/handover.py --status
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

LAB = Path(__file__).resolve().parent
REPO = LAB.parents[1]
CONFIG = LAB / "handover.json"
BATCH_MAX = 20  # the contract: up to 20 posts per batch
SUPPORTED_SPEC = "publishing-spec/v1"  # contracts/registry.yaml publishing.spec
SUPPORTED_MANIFEST = 1  # contracts/registry.yaml reels.manifest
MEDIA_FRESH_DAYS = 5  # the server keeps an upload a week after its posts are done; re-upload after 5 days to be safe
RETRY_STATUS = {429, 502, 503, 504}
UPLOAD_CHUNK = 256 * 1024  # files are streamed: the write timeout applies per chunk, so any size works on a slow line
GONE_MEDIA = {"media_expired", "unknown_media"}  # the server no longer has a file we uploaded: upload it again
BACKOFF = (0.5, 1.5, 4.0)

# What each kind of render becomes on each platform: (render kind, size or format) → platform → publishing format.
# One 9:16 master serves every short-video platform; the publisher makes its own renditions.
ROUTES: dict[tuple[str, str], dict[str, str]] = {
    ("video", "9:16"): {
        "instagram": "reel",
        "facebook": "video",
        "tiktok": "video",
        "youtube": "video",
        "threads": "video",
    },
    ("video", "4:5"): {"facebook": "video", "threads": "video"},
    ("slides", "carousel"): {"instagram": "carousel", "facebook": "photo", "threads": "image"},
    ("slides", "album"): {"instagram": "carousel", "facebook": "photo", "threads": "image"},
    ("slides", "story"): {"instagram": "story"},
}


def _retry_after(r: httpx.Response) -> float:
    try:
        return min(float(r.headers.get("Retry-After", 0) or 0), 30.0)
    except ValueError:  # an HTTP date instead of seconds: just use our own backoff
        return 0.0


class PublishError(Exception):
    """A refused or failed call. Carries the problems the API listed; the key is never in the message."""

    def __init__(self, status: int, message: str, problems: list[dict[str, Any]] | None = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.problems = problems or []


@dataclass
class Held:
    """Something that did not go out, and why. Reported to the owner; never silently dropped."""

    job_id: str
    platform: str | None
    code: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "platform": self.platform, "code": self.code, "reason": self.reason}


@dataclass
class Report:
    would_submit: list[str] = field(default_factory=list)  # dry run: post_keys that validated
    submitted: list[str] = field(default_factory=list)  # post_keys created now
    replayed: list[str] = field(default_factory=list)  # post_keys that already existed
    held: list[Held] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)  # refused by the API: post_key + problems
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "would_submit": self.would_submit,
            "submitted": self.submitted,
            "replayed": self.replayed,
            "held": [h.as_dict() for h in self.held],
            "failed": self.failed,
        }


# ---------- configuration ----------


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    cfg: dict[str, Any] = {"reviewed_languages": [], "defaults": {}}
    if path.exists():
        cfg.update(json.loads(path.read_text()))
    return cfg


def load_env(path: Path = REPO / ".env") -> dict[str, str]:
    """DK_PUBLISH_URL and DK_PUBLISH_KEY from the environment, else from .env. Nothing else is read from .env."""
    out = {k: os.environ[k] for k in ("DK_PUBLISH_URL", "DK_PUBLISH_KEY") if os.environ.get(k)}
    if path.exists() and len(out) < 2:
        for line in path.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() in ("DK_PUBLISH_URL", "DK_PUBLISH_KEY") and key.strip() not in out and value.strip():
                out[key.strip()] = value.strip().strip("'\"")
    return out


# ---------- the HTTP client ----------


class PublishClient:
    """A thin client for the publishing API. Every call is safe to repeat (reads, content-addressed uploads, and
    idempotent batches), so transient failures are retried with backoff; a refusal (4xx) never is."""

    def __init__(self, base_url: str, key: str, transport: httpx.BaseTransport | None = None, sleep: Any = time.sleep):
        self._key = key
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), transport=transport, timeout=httpx.Timeout(60.0, read=300.0)
        )

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "[key]") if self._key else text

    def _call(self, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._key}", **kw.pop("headers", {})}
        last: Exception | None = None
        body = kw.pop("content_factory", None)  # a fresh body per attempt: a retry never re-sends a spent stream
        for wait in (*BACKOFF, None):
            try:
                r = self._http.request(method, url, headers=headers, **({"content": body()} if body else {}), **kw)
            except httpx.TransportError as e:  # connection trouble: nothing reached the server's logic, safe to repeat
                last = PublishError(0, self._scrub(f"could not reach the publishing API ({type(e).__name__})"))
            else:
                if r.status_code in RETRY_STATUS and wait is not None:
                    last = PublishError(r.status_code, "temporarily unavailable")
                    wait = max(wait, _retry_after(r))
                elif r.status_code >= 400:
                    raise self._error(r)
                else:
                    return r
            if wait is None:
                break
            self._sleep(wait)
        assert last is not None
        raise last

    def _error(self, r: httpx.Response) -> PublishError:
        try:
            body = r.json()
        except ValueError:
            body = {}
        title = body.get("title") or body.get("detail") or r.reason_phrase
        return PublishError(r.status_code, self._scrub(str(title)), body.get("problems"))

    def spec(self, cache: Path | None = None, fresh: bool = False) -> dict[str, Any]:
        """GET /v1/spec, cached by its version (ETag) so an unchanged contract costs one 304. `fresh`: the publisher
        must answer now (a run-start check); otherwise an outage falls back to the cached contract."""
        headers: dict[str, str] = {}
        cached: dict[str, Any] | None = None
        if cache and cache.exists():
            cached = json.loads(cache.read_text())
            headers["If-None-Match"] = f'"{cached["spec_version"]}"'
        try:
            r = self._call("GET", "/v1/spec", headers=headers)
        except PublishError:
            if cached and not fresh:  # offline or unavailable: the last contract is still the best we know
                return cached
            raise
        if r.status_code == 304 and cached:
            return cached
        doc: dict[str, Any] = r.json()
        if doc.get("spec") != SUPPORTED_SPEC:  # a contract this code doesn't speak: stop, never guess
            raise PublishError(
                0, f"the publisher speaks {doc.get('spec')!r}, this handover {SUPPORTED_SPEC!r}: "
                "run `uv run intel contracts sync`, then update the handover",
            )  # fmt: skip
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(doc))
        return doc

    def upload(self, path: Path) -> str:
        """Streamed in chunks (measured 2026-10-12: a home line sends about 224 KB/s, so an 18 MB reel takes 80 s; sent
        as one write it hit the 60 s write timeout and every retry started over)."""

        def chunks() -> Any:
            with path.open("rb") as f:
                while block := f.read(UPLOAD_CHUNK):
                    yield block

        r = self._call(
            "POST",
            "/v1/media",
            params={"filename": path.name},
            content_factory=chunks,
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size)},
        )
        media_id = r.json().get("media_id")
        if not media_id:
            raise PublishError(r.status_code, "the upload answer had no media_id")
        return str(media_id)

    def batch(self, posts: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
        return self._call("POST", "/v1/posts/batch", json={"dry_run": dry_run, "posts": posts}).json()

    def post(self, post_key: str) -> dict[str, Any] | None:
        """GET /v1/posts/{post_key}: the post, or None when there is none (so a slot isn't made twice)."""
        try:
            return self._call("GET", f"/v1/posts/{post_key}").json()
        except PublishError as e:
            if e.status == 404:
                return None
            raise

    def overview(self, days_back: int = 7, days_ahead: int = 14) -> dict[str, Any]:
        return self._call("GET", "/v1/overview", params={"days_back": days_back, "days_ahead": days_ahead}).json()

    def calendar(self, **filters: Any) -> dict[str, Any]:
        return self._call("GET", "/v1/calendar", params=filters).json()


# ---------- media: upload once per file content ----------


class MediaLedger:
    """content hash → media_id, so a re-run (or a second platform) never uploads the same file twice."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict[str, Any]] = json.loads(path.read_text()) if path.exists() else {}

    def get(self, sha: str, now: dt.datetime) -> str | None:
        e = self.data.get(sha)
        if e and now - dt.datetime.fromisoformat(e["at"]) < dt.timedelta(days=MEDIA_FRESH_DAYS):
            return str(e["media_id"])
        return None

    def put(self, sha: str, media_id: str, now: dt.datetime) -> None:
        self.data[sha] = {"media_id": media_id, "at": now.isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))

    def forget(self, media_id: str) -> None:
        self.data = {k: v for k, v in self.data.items() if v["media_id"] != media_id}
        self.path.write_text(json.dumps(self.data, indent=1))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- planning: pure, no I/O ----------


def _kind_size(manifest: dict[str, Any]) -> tuple[str, str] | None:
    files = manifest["files"]
    if manifest["kind"] == "video":
        formats = {f["format"] for f in files}
        return ("video", "9:16" if "9:16" in formats else "4:5" if "4:5" in formats else sorted(formats)[0])
    size = files[0].get("size") if files else None
    return ("slides", size) if size else None


def _files_for(manifest: dict[str, Any], kind_size: tuple[str, str]) -> list[str]:
    if kind_size[0] == "video":
        return [f["path"] for f in manifest["files"] if f["format"] == kind_size[1]]
    return sorted(f["path"] for f in manifest["files"])


def _title(manifest: dict[str, Any], limit: int) -> str:
    """A title for platforms that require one: the on-screen hook, else the job id."""
    hook = next((t["text"] for t in manifest.get("on_screen_text", []) if t["where"] == "hook"), None)
    return str(hook or manifest["job_id"].replace("-", " "))[:limit]


def _within(slot: dt.datetime, now: dt.datetime, window: dict[str, Any]) -> str | None:
    lead = (slot - now).total_seconds()
    if lead < window["min_lead_s"]:
        return f"the slot is {int(lead)} s away; the publisher needs at least {window['min_lead_s']} s"
    if lead > window["max_lead_s"]:
        return f"the slot is more than {window['max_lead_s'] // 86400} days away"
    return None


def plan_post(
    manifest: dict[str, Any],
    media_ids: dict[str, str],
    spec: dict[str, Any],
    cfg: dict[str, Any],
    now: dt.datetime,
) -> tuple[dict[str, Any] | None, list[Held]]:
    """One manifest → one post with a variant per platform that can take it, plus everything held and why.
    `media_ids` maps a file name in the manifest to its uploaded id (the caller uploads only what is needed)."""
    job = manifest["job_id"]
    held: list[Held] = []

    def hold(platform: str | None, code: str, reason: str) -> None:
        held.append(Held(job, platform, code, reason))

    if manifest.get("manifest_version") != SUPPORTED_MANIFEST:
        hold(None, "contract", f"manifest_version {manifest.get('manifest_version')!r}, this handover reads "
             f"{SUPPORTED_MANIFEST}: run `uv run intel contracts sync`")  # fmt: skip
        return None, held
    if manifest["status"] == "qa_failed":
        hold(
            None,
            "qa_failed",
            "the render failed its quality checks: " + "; ".join(q["check"] for q in manifest["qa"] if not q["ok"]),
        )
        return None, held
    if manifest["status"] == "needs_review":
        unreviewed = sorted(set(manifest["language"]) - {"en", "de"} - set(cfg.get("reviewed_languages", [])))
        if unreviewed:
            hold(
                None,
                "needs_review",
                f"a native speaker has not checked the on-screen text yet ({', '.join(unreviewed)}); "
                "add the language to handover.json → reviewed_languages once it has been checked",
            )
            return None, held
    pattern = spec["post"]["post_key_pattern"]
    if not re.fullmatch(pattern, job):
        hold(None, "bad_post_key", f"job id '{job}' doesn't match the publisher's post_key rule {pattern}")
        return None, held

    publish = manifest.get("publish") or {}
    platforms = publish.get("platforms") or []
    if not platforms:
        hold(None, "no_platforms", "the spec names no platforms to publish on")
        return None, held
    at = publish.get("at")
    try:
        slot = dt.datetime.fromisoformat(at) if at else None
    except ValueError:
        slot = None
    if slot is None or slot.tzinfo is None:
        hold(None, "bad_slot", "the slot is missing or has no UTC offset (e.g. 2026-10-14T16:30:00+02:00)")
        return None, held
    why = _within(slot, now, spec["post"]["slot"])
    if why:
        hold(None, "slot_window", why)
        return None, held

    ks = _kind_size(manifest)
    routes = ROUTES.get(ks or ("", ""), {})
    variants: list[dict[str, Any]] = []
    for platform in platforms:
        info = spec["platforms"].get(platform)
        if info is None:
            hold(platform, "platform_off", f"{platform} is not switched on in the publisher")
            continue
        fmt = routes.get(platform)
        names = _files_for(manifest, ks) if ks else []
        if fmt == "carousel" and len(names) == 1:
            fmt = "feed"  # one picture is a feed post, not a carousel
        if fmt is None or fmt not in info["formats"]:
            hold(
                platform,
                "no_format",
                f"{platform} has no format for a {ks[0] if ks else 'unknown'} {ks[1] if ks else ''} render".strip(),
            )
            continue
        rules = info["formats"][fmt]
        n = len(names)
        if not (rules["media"]["min"] <= n <= rules["media"]["max"]):
            hold(
                platform,
                "media_count",
                f"{platform} {fmt} takes {rules['media']['min']}-{rules['media']['max']} files; this render has {n}",
            )
            continue
        caption_rule = rules.get("caption")
        caption = (manifest.get("captions") or {}).get(platform, "")
        if caption_rule:
            limit = caption_rule.get("max_length")
            if caption_rule.get("required") and not caption:
                hold(platform, "caption_missing", f"{platform} needs a caption and the spec has none for it")
                continue
            if limit and len(caption) > limit:
                hold(
                    platform,
                    "caption_too_long",
                    f"the {platform} caption is {len(caption)} characters; the limit is {limit}",
                )
                continue
        options: dict[str, Any] = dict(cfg.get("defaults", {}).get(platform, {}))
        options.update((publish.get("options") or {}).get(platform, {}))
        if "title" in rules["options"] and "title" not in options:
            options["title"] = _title(manifest, rules["options"]["title"].get("max_length") or 100)
        missing = [k for k, f in rules["options"].items() if f["required"] and k not in options]
        if missing:
            hold(
                platform,
                "option_missing",
                f"{platform} {fmt} needs {', '.join(missing)} and nothing supplies it (handover.json → defaults)",
            )
            continue
        bad = [
            k
            for k, v in options.items()
            if k in rules["options"] and rules["options"][k].get("choices") and v not in rules["options"][k]["choices"]
        ]
        if bad:
            hold(platform, "option_invalid", f"{platform} {fmt}: {', '.join(bad)} is not one of the allowed choices")
            continue
        v: dict[str, Any] = {
            "platform": platform,
            "format": fmt,
            "media": [media_ids[x] for x in names],
            "slot": slot.isoformat(),
        }
        if caption_rule and caption:
            v["caption"] = caption
        if options:
            v["options"] = options
        variants.append(v)
    if not variants:
        return None, held
    return {"post_key": job, "variants": variants}, held


# ---------- submitting ----------


def _needed_files(manifest: dict[str, Any]) -> list[str]:
    ks = _kind_size(manifest)
    return _files_for(manifest, ks) if ks else []


class _Expired(Exception):
    """The server no longer has an uploaded file a post names (it is kept a week after its posts finish)."""

    def __init__(self, post_keys: list[str], media_ids: list[str]):
        super().__init__("media expired")
        self.post_keys = post_keys
        self.media_ids = media_ids


def submit(
    client: PublishClient,
    jobs: list[tuple[dict[str, Any], Path]],
    spec: dict[str, Any],
    cfg: dict[str, Any],
    ledger: MediaLedger,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> Report:
    """jobs: (manifest, the folder its files are in). A dry run validates with the publisher and creates nothing; a real
    run does a dry run first, then creates only what validated. An upload that aged out on the server is forgotten and
    uploaded again, once."""
    now = now or dt.datetime.now(dt.UTC)
    try:
        return _run(client, jobs, spec, cfg, ledger, now, dry_run)
    except _Expired as e:
        for media_id in e.media_ids:
            ledger.forget(media_id)
        try:
            return _run(client, jobs, spec, cfg, ledger, now, dry_run)
        except _Expired as again:
            raise PublishError(409, f"uploads expired again for {', '.join(again.post_keys)}") from again


def _run(
    client: PublishClient,
    jobs: list[tuple[dict[str, Any], Path]],
    spec: dict[str, Any],
    cfg: dict[str, Any],
    ledger: MediaLedger,
    now: dt.datetime,
    dry_run: bool,
) -> Report:
    report = Report(dry_run=dry_run)
    posts: list[dict[str, Any]] = []
    for manifest, folder in jobs:
        names = _needed_files(manifest)
        # cheap gates first: don't upload files for a job that will be held anyway
        probe, held = plan_post(manifest, {n: "probe" for n in names}, spec, cfg, now)
        report.held.extend(held)
        if probe is None:
            continue
        media: dict[str, str] = {}
        for name in names:
            sha = _sha(folder / name)
            media_id = ledger.get(sha, now)
            if media_id is None:
                media_id = client.upload(folder / name)
                ledger.put(sha, media_id, now)
            media[name] = media_id
        post, _ = plan_post(manifest, media, spec, cfg, now)
        assert post is not None
        posts.append(post)

    def send(chunk: list[dict[str, Any]], dry: bool) -> dict[str, dict[str, Any]]:
        results = {r["post_key"]: r for r in client.batch(chunk, dry)["results"]}
        expired = [
            k
            for k, r in results.items()
            if not r["ok"] and any(p.get("code") in GONE_MEDIA for p in r.get("problems", []))
        ]
        if expired:
            ids = [m for p in chunk if p["post_key"] in expired for v in p["variants"] for m in v["media"]]
            raise _Expired(expired, ids)
        return results

    for i in range(0, len(posts), BATCH_MAX):
        chunk = posts[i : i + BATCH_MAX]
        results = send(chunk, True)
        valid = [p for p in chunk if results[p["post_key"]]["ok"]]
        if valid and not dry_run:
            results.update(send(valid, False))
        for p in chunk:
            r = results[p["post_key"]]
            if not r["ok"]:
                report.failed.append({"post_key": p["post_key"], "problems": r.get("problems", [])})
            elif dry_run:
                report.would_submit.append(p["post_key"])
            elif r.get("replayed"):
                report.replayed.append(p["post_key"])
            else:
                report.submitted.append(p["post_key"])
    return report


# ---------- status (code reads this; an AI never polls) ----------


def status(client: PublishClient, days_back: int = 7, days_ahead: int = 14) -> dict[str, Any]:
    """What was approved, published or failed, from GET /v1/overview. Small enough for the weekly report."""
    o = client.overview(days_back, days_ahead)
    return {
        "pipeline": o.get("pipeline"),
        "published": (o.get("published") or {}).get("total"),
        "problems": o.get("problems"),
        "awaiting_approval": len(o.get("awaiting_approval") or []),
        "plan_ends": o.get("plan_ends"),
        "note": o.get("note"),
    }


# ---------- command line ----------


def _client() -> PublishClient:
    env = load_env()
    if "DK_PUBLISH_URL" not in env or "DK_PUBLISH_KEY" not in env:
        sys.exit(
            "Set DK_PUBLISH_URL and DK_PUBLISH_KEY (a key with the 'draft' right) in .env. They are never printed."
        )
    return PublishClient(env["DK_PUBLISH_URL"], env["DK_PUBLISH_KEY"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--manifest", action="append", default=[], help="a manifest.json from dk_reels (repeat for several)"
    )
    ap.add_argument("--dry-run", action="store_true", help="validate with the publisher, create nothing")
    ap.add_argument("--status", action="store_true", help="print where publishing stands")
    args = ap.parse_args()
    client = _client()
    if args.status:
        print(json.dumps(status(client), indent=1))
        return
    if not args.manifest:
        sys.exit("give --manifest (or --status)")
    spec = client.spec(cache=REPO / "data" / "lab" / "publish-spec.json")
    jobs = [(json.loads(Path(m).read_text()), Path(m).parent) for m in args.manifest]
    report = submit(
        client,
        jobs,
        spec,
        load_config(),
        MediaLedger(REPO / "data" / "lab" / "media-ledger.json"),
        dry_run=args.dry_run,
    )
    print(json.dumps(report.as_dict(), indent=1))


if __name__ == "__main__":
    main()
