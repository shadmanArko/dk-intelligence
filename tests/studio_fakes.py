"""Test doubles for the studio's four ports. They prove the wiring (what is called, in what order, with what limits),
not the world: the real run with the real model, renderer and publisher is by hand (docs/results/S2_studio.md)."""

from __future__ import annotations

import copy
import datetime as dt
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from intel.studio import handover
from intel.studio.ports import AgentResult
from intel.studio.runlog import RunLog
from tests.test_lab_handover import KEY, SPEC, FakePublisher

NOW = dt.datetime(2026, 10, 12, 18, 0, tzinfo=dt.UTC)
TODAY = dt.date(2026, 10, 12)
PACK: dict[str, Any] = {
    "competitor_gaps": [{"aspect": "delivery_time", "quote": {"cite": "R-AAAAAAAA", "text": "Essen kam zu spät"}}],
    "our_best_posts": [
        {"cite": "P-GOOD0001", "text": "Mutton Kacchi, slow dum, Berlin", "perf": {"vs_our_median": 3.5}}
    ],
    "opportunities": [],
    "brand_reminders": ["always say: Kacchi, Berlin"],
}
VOCAB = {"video_categories": ["Eating"], "music_cultures": ["desi"], "music_moods": ["hip-hop"],
         "templates": {"slides": {"photo": "x"}}, "contracts": {"content_spec": 1, "manifest": 1}}  # fmt: skip
INVENTORY = {"video": {"Pot opening": "3, 1 key moments"}, "photo": {"Eating": "2"}}
# the publisher's spec as the real one states it, plus the youtube description the real spec has
PUBLISH_SPEC = copy.deepcopy(SPEC)
PUBLISH_SPEC["platforms"]["youtube"]["formats"]["video"]["options"]["description"] = {
    "kind": "text", "required": False, "max_length": 5000}  # fmt: skip
PUBLISH_SPEC["platforms"]["threads"]["formats"]["video"]["caption"]["max_length"] = 500


class FakeTools:
    """dk_intel and dk_reels as code calls them. `low` = probe refs the scorer puts below the bar."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.low: set[str] = set()
        self.bad_text: str | None = None  # a word dk_intel blocks on screen
        self.vocab = copy.deepcopy(VOCAB)
        self._lock = threading.Lock()

    def offered(self, server: str) -> list[str]:
        tools = {
            "dk_intel_writer": ["evaluate_drafts", "get_facts", "brand_rules"],
            "dk_reels_creative": ["list_templates", "inventory", "preview_spec", "search_assets"],
        }
        return tools.get(server, [])

    def call(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        with self._lock:
            self.calls.append((server, tool, args))
        if tool == "grounding_pack":
            return copy.deepcopy(PACK)
        if tool == "brand_rules":
            rng = [0, 1] if args["platform"] == "threads" else [3, 5]
            return {"platform": {"first_line_max": 60, "emoji_max": 4, "formats": {"reel": {"hashtags": rng}}}}
        if tool == "get_facts":
            return {
                "items": [
                    {"key": "menu.lamb_box.name", "value": "Smokey Lamb Box", "status": "confirmed"},
                    {"key": "menu.lamb_box.available", "value": None, "status": "unknown"},
                    {"key": "menu.mutton_box.name", "value": "Mutton Box", "status": "confirmed"},
                    {"key": "menu.mutton_box.available", "value": True, "status": "confirmed"},
                ]
            }
        if tool == "list_templates":
            return self.vocab
        if tool == "inventory":
            return {"video": {"Pot opening": {"n": 3, "star1": 1, "star2": 1}}, "photo": {"Eating": {"n": 2}}}
        if tool == "evaluate_drafts":
            assert 1 <= len(args["drafts"]) <= 20, "dk_intel takes at most 20 drafts per call"
            return {"results": [self._judge(d) for d in args["drafts"]]}
        raise AssertionError(f"unexpected tool {tool}")

    def _judge(self, d: dict[str, Any]) -> dict[str, Any]:
        ref, text = d["ref"], d["caption"]
        if ref.startswith("p"):  # a probe: the scorer's answer for a slot
            below = ref in self.low
            score = {"probability": 0.45 if below else 0.6, "above": not below}
            return {"ref": ref, "verdict": "rewrite" if below else "keep", "errors": [], "warnings": [],
                    "scores": {"content_scorer": score}}  # fmt: skip
        if "BAD" in text or (self.bad_text and self.bad_text in text.lower()):
            return {"ref": ref, "verdict": "blocked", "errors": [{"code": "HALAL_UNCERTIFIED"}], "warnings": [],
                    "fixes": ["say what the facts allow"]}  # fmt: skip
        if "WARN" in text:
            return {"ref": ref, "verdict": "rewrite", "errors": [], "warnings": [{"code": "MISSING_BERLIN"}],
                    "fixes": ["add Berlin"]}  # fmt: skip
        return {"ref": ref, "verdict": "keep", "errors": [], "warnings": []}


class FakeRunner:
    """The agents. Same answers every time, 1,200 tokens a call; failures are injected by id."""

    def __init__(self, log: RunLog | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail: set[str] = set()  # node names that fail
        self.bad_platforms: set[str] = set()  # the adapter's FIRST answer for these platforms breaks a brand rule
        self.dup_first = False  # the strategist's first answer repeats one angle
        self.tokens = 1200
        self.active = 0
        self.max_active = 0
        self.delay = 0.0
        self._lock = threading.Lock()
        self._strategist_calls = 0

    def run(self, node: str, agent: str, task: str) -> AgentResult:
        t = json.loads(task)
        with self._lock:
            self.calls.append({"node": node, "agent": agent, "task": t})
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        if node in self.fail:
            return AgentResult(None, 100, 0, error="simulated failure")
        return AgentResult(getattr(self, f"_{agent}")(t), self.tokens - 200, 200, 1, 0.1)

    def _strategist(self, t: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._strategist_calls += 1
            first = self._strategist_calls == 1
        pillars = [p["name"] for p in t["pillars"]]
        slots = []
        for k, s in enumerate(t["slots"]):
            angle = f"angle number {s['slot_id']} about food" if not (self.dup_first and first and k == 1) else \
                f"angle number {t['slots'][0]['slot_id']} about food"  # fmt: skip
            slots.append(
                {
                    "id": s["slot_id"],
                    "angle": angle,
                    "pillar": pillars[k % len(pillars)],
                    "sources": ["R-AAAAAAAA", "R-MADEUP00"],
                }
            )  # `id`, as the real planner once answered
        return {"slots": slots}

    def _copywriter(self, t: dict[str, Any]) -> dict[str, Any]:
        return {"slot_id": t["slot_id"], "caption": f"Samstag Kacchi in Berlin {t['slot_id']}", "verdict": "keep",
                "score": 0.6, "rounds": 1, "tried": 2, "sources": t["sources"]}  # fmt: skip

    def _adapter(self, t: dict[str, Any]) -> dict[str, Any]:
        repair = t.get("repair")
        captions: dict[str, str] = {}
        options: dict[str, Any] = {}
        for p, info in t["platforms"].items():
            if repair is not None and p not in repair:
                continue
            word = "BAD" if (p in self.bad_platforms and repair is None) else "ok"
            if info["caption_max"] is None:
                options[p] = {"title": f"Kacchi in Berlin {word}",
                              "description": f"Kacchi Berlin {t['idea_id']} {word}",
                              "privacy_level": "public"}  # privacy is code's: must be dropped  # fmt: skip
            else:
                captions[p] = f"{p} Kacchi Berlin {t['idea_id']} {word}"
        return {"idea_id": t["idea_id"], "captions": captions, "options": options}

    def _designer(self, t: dict[str, Any]) -> dict[str, Any]:
        if t["make"] == "video":
            content = {"kind": "video", "template": "beat-reel", "hook": "Sealed. Then opened.",
                       "hook_sub": "Deckel ab",
                       "music": {"track": "auto", "culture": "desi", "mood": ["hip-hop"], "pre_drop_beats": 8},
                       "shots": [{"category": "Marination"}, {"category": "Pot opening", "on": "drop", "min_rating": 2,
                                                             "caption": "Lid off", "sub": "Deckel ab"},
                                 {"category": "Eating"}]}  # fmt: skip
        else:
            slides = [
                {"template": "photo", "category": "Eating", "title": "Lid off"},
                {"template": "card", "title": "Order"},
            ]
            content = {"kind": "slides", "size": "carousel", "slides": slides}
        return {"job_id": t["job_id"], "spec": {"spec_version": 1, "job_id": t["job_id"], "content": content,
                                                "captions": {"instagram": "must be ignored"},
                                                "publish": {"platforms": ["tiktok"],
                                                            "at": "2020-01-01T00:00:00+00:00"}},
                "why": "the lid"}  # fmt: skip


class FakeRenderer:
    """The reels CLI: a dry run lists the on-screen words and the footage; a render writes a real-shaped manifest."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0
        self.fail_render: set[str] = set()
        self._lock = threading.Lock()

    def _plan(self, spec: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        c = spec["content"]
        n = int(spec["job_id"].rsplit("i", 1)[-1][0]) * 10 + len(self.calls)
        plan = {"shots": [{"asset": f"v-c{n:04d}-0", "src": f"footage/k/c{n:04d}.mp4"}]}
        texts = [{"where": "hook", "text": c.get("hook") or c["slides"][0]["title"]}]
        texts += [{"where": f"captions.{k}", "text": v} for k, v in spec["captions"].items()]
        return plan, texts

    def dry_run(self, spec_path: Path) -> tuple[int, dict[str, Any]]:
        spec = json.loads(spec_path.read_text())
        self.calls.append(("dry", spec["job_id"]))
        plan, texts = self._plan(spec)
        return 0, {"ok": True, "dry_run": True, "plan": plan, "texts": texts}

    def render(self, spec_path: Path) -> tuple[int, dict[str, Any]]:
        spec = json.loads(spec_path.read_text())
        jid = spec["job_id"]
        with self._lock:
            self.calls.append(("render", jid))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.02)
        with self._lock:
            self.active -= 1
        if jid in self.fail_render:
            return 3, {"ok": False, "error": "ffmpeg crashed"}
        folder = self.out / jid
        folder.mkdir(parents=True, exist_ok=True)
        plan, texts = self._plan(spec)
        file = {"width": 1080, "height": 1920, "bytes": 1, "sha256": "a" * 64}
        if spec["content"]["kind"] == "video":
            (folder / f"{jid}_9x16.mp4").write_bytes(b"video-" + jid.encode())
            files = [{"path": f"{jid}_9x16.mp4", "kind": "video", "format": "9:16", **file}]
            kind, template = "video", "beat-reel"
        else:
            names = [f"{jid}-{k + 1:02d}.jpg" for k in range(1 if spec["content"]["size"] == "album" else 2)]
            for x in names:
                (folder / x).write_bytes(b"img-" + x.encode())
            files = [{"path": x, "kind": "image", "size": spec["content"]["size"], **file} for x in names]
            kind, template = "slides", f"slides:{spec['content']['size']}"
        manifest = {"manifest_version": 1, "campaign": spec["campaign"], "spec_hash": "h", "created_at": "t",
                    "template": template, "review": [], "job_id": jid, "status": "ready", "kind": kind,
                    "language": spec["language"], "qa": [], "files": files, "plan": plan, "on_screen_text": texts,
                    "captions": spec["captions"], "publish": spec["publish"]}  # fmt: skip
        (folder / "manifest.json").write_text(json.dumps(manifest))
        return 0, {"ok": True, "job_id": jid, "status": "ready", "manifest": str(folder / "manifest.json")}

    def thumbnail(self, manifest: dict[str, Any], folder: Path, out: Path) -> bool:
        return False


def fake_publisher() -> tuple[handover.PublishClient, FakePublisher]:
    fake = FakePublisher()
    fake.spec_doc = PUBLISH_SPEC  # type: ignore[attr-defined]
    original = FakePublisher.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/spec":
            return httpx.Response(200, json=PUBLISH_SPEC)
        return original(fake, request)

    client = handover.PublishClient("https://publish.test", KEY, transport=httpx.MockTransport(handler),
                                    sleep=lambda s: None)  # fmt: skip
    return client, fake
