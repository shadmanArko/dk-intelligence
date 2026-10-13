"""The studio's orchestrator: plain code that turns a plan into drafts. AI judges inside the steps; code runs the steps.

    start checks (publisher, reels contracts: before any AI)
      → inputs (pack, scorer answers, vocabulary, prices: 0 tokens)
      → ideas (days × windows from the scorer; real dates)
      → strategist (angles, in chunks)
      → per idea, 3 at a time: copywriter → adapter → designer ─► prepare (spec, dry run, on-screen words)
                                                                      │
                                         render queue (one at a time) ┘─► upload queue (as each render finishes)
      → hand over to dk-publishing (dry run first, then drafts) → report

Modes (`publish`): render = media only, nothing sent anywhere · dry-run = also validate with the publisher and create
nothing · drafts = create drafts the owner approves. Every idea that does not make it is reported with its reason.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import json
import os
import queue
import shutil
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from intel.studio import checks, handover, media, slots
from intel.studio.ai import AI
from intel.studio.budget import Budget
from intel.studio.config import StudioConfig
from intel.studio.crew import Crew
from intel.studio.inputs import fetch_inputs
from intel.studio.plan import plan_angles
from intel.studio.ports import PublisherPort, RendererPort, RunnerPort, ToolPort
from intel.studio.render import keep_awake
from intel.studio.runlog import RunLog, short
from intel.studio.state import RunState

MODES = ("render", "dry-run", "drafts")


@dataclasses.dataclass
class Options:
    publish: str = "render"
    trigger: str = "manual"
    inputs: Path | None = None  # replay frozen inputs instead of fetching
    save_inputs: Path | None = None  # freeze this run's inputs
    resume: str | None = None  # a run id: continue it
    suffix: str = ""  # redo ideas whose post was rejected: they need a new key, e.g. r2
    today: dt.date | None = None


@dataclasses.dataclass
class Studio:
    cfg: StudioConfig
    tools: ToolPort
    make_runner: Callable[[RunLog], RunnerPort]
    renderer: RendererPort
    publisher: PublisherPort | None
    data_dir: Path
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC)
    stay_awake: Callable[[], contextlib.AbstractContextManager[None]] = keep_awake

    # ---------- entry ----------

    def run(self, o: Options) -> int:
        if o.publish not in MODES:
            raise ValueError(f"publish must be one of {MODES}")
        runs = self.data_dir / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        lock = self.data_dir / ".running"
        if not self._take_lock(lock):
            print("a run is already in progress")
            return 2
        try:
            run_id = o.resume or self.now().strftime("%Y%m%dT%H%M%SZ")
            folder = runs / run_id
            if o.resume and not (folder / "state.json").exists():
                print(f"no run {run_id} to resume")
                return 2
            lock.write_text(f"{os.getpid()} {run_id}")
            self.log = RunLog(folder, run_id)
            self.state = RunState(folder / "state.json")
            self.opts = o
            return self._run(folder, run_id)
        finally:
            lock.unlink(missing_ok=True)

    @staticmethod
    def _take_lock(lock: Path) -> bool:
        if lock.exists():
            with contextlib.suppress(ValueError, OSError):
                os.kill(int(lock.read_text().split()[0]), 0)  # the holder is alive: refuse
                return False
        lock.write_text(str(os.getpid()))
        return True

    # ---------- phases ----------

    @contextlib.contextmanager
    def phase(self, name: str, **data: Any) -> Iterator[None]:
        t0 = time.monotonic()
        self.log.phase(name, "start", **data)
        try:
            yield
        finally:
            took = round(time.monotonic() - t0, 1)
            self.phase_seconds[name] = self.phase_seconds.get(name, 0.0) + took
            self.log.phase(name, "end", seconds=took)

    def _fail(self, why: str) -> int:
        self.log.emit("orchestrator", "end", status="failed", error=short(why, 400))
        print(f"failed: {why}")
        return 1

    def _run(self, folder: Path, run_id: str) -> int:
        o, cfg, log, state = self.opts, self.cfg, self.log, self.state
        self.phase_seconds: dict[str, float] = {}
        self.t0 = time.monotonic()
        self.hcfg = cfg.handover_cfg()
        self.mledger = handover.MediaLedger(self.data_dir / "media-ledger.json", fresh_days=cfg.plan.horizon_days + 4)
        self.aledger = media.AssetLedger(self.data_dir / "asset-ledger.json",
                                         recent_jobs=media.RECENT_JOBS + cfg.plan.max_ideas_per_run)  # fmt: skip
        (folder / "config.json").write_text(json.dumps(cfg.snapshot(), indent=1))
        log.emit("orchestrator", "start", trigger=o.trigger, publish=o.publish, config=cfg.digest(),
                 resume=bool(o.resume), plan=f"{cfg.plan.horizon_days} days × {cfg.plan.ideas_per_day} ideas × "
                 f"{len(cfg.plan.platforms)} platforms ({', '.join(cfg.plan.platforms)})")  # fmt: skip
        budget = Budget(cfg.budgets.max_tokens_per_run, cfg.budgets.alert_at, cfg.budgets.max_ai_calls_per_idea)
        self.budget = budget
        self.ai = AI(self.make_runner(log), budget, log)

        # 1. start checks: before a single token is spent
        if o.publish != "render" and self.publisher is None:
            return self._fail("DK_PUBLISH_URL and DK_PUBLISH_KEY are not set (needed for dry-run and drafts)")
        publish_spec: dict[str, Any] = {}
        if self.publisher is not None:
            try:  # reachable and speaking our contract? asked now, fresh
                publish_spec = self.publisher.spec(cache=self.data_dir / "publish-spec.json", fresh=True)
            except handover.PublishError as ex:
                if o.publish != "render":
                    return self._fail(f"the publisher is not usable: {short(str(ex), 200)}")

        # 2. inputs
        with self.phase("inputs"):
            try:
                inputs = self._inputs(folder)
            except Exception as ex:  # noqa: BLE001 - no inputs, no run: end it with the reason
                return self._fail(f"could not fetch the inputs: {short(str(ex))}")
        if why := media.contract_problem(inputs["vocab"]):
            return self._fail(why)
        publish_spec = publish_spec or inputs.get("publish_spec") or self._cached_spec()
        if not publish_spec:
            return self._fail("no publishing spec: run once with DK_PUBLISH_URL and DK_PUBLISH_KEY set")
        inputs["publish_spec"] = publish_spec
        self.inputs, self.publish_spec = inputs, publish_spec

        # 3. ideas
        ideas = self._ideas(inputs)
        log.emit("orchestrator", "ideas", planned=len(ideas), dropped=state.data["dropped"])
        if not ideas:
            log.emit("orchestrator", "end", status="done", note="no slot can pass the check: nothing to write")
            return 0
        if o.publish != "render":
            self._skip_existing(ideas)

        # 4. angles
        with self.phase("strategist", ideas=len(ideas)):
            plan_angles(
                self.ai,
                cfg,
                state,
                log,
                ideas,
                inputs["pack"],
                inputs.get("unavailable", []),
                inputs.get("performance"),
            )

        # 5. the crews, with renders and uploads running behind them
        crew = self.crew = Crew(self.ai, self.tools, cfg, state, log, inputs, publish_spec)
        with self.phase("production", ideas=len(ideas)), self.stay_awake():
            self._produce(crew, ideas, folder)

        # 6. hand over
        with self.phase("handover"):
            self._handover(ideas, folder, publish_spec)
        return self._finish(ideas, folder, run_id)

    # ---------- inputs and ideas ----------

    def _cached_spec(self) -> dict[str, Any]:
        path = self.data_dir / "publish-spec.json"
        return dict(json.loads(path.read_text())) if path.exists() else {}

    def _inputs(self, folder: Path) -> dict[str, Any]:
        o = self.opts
        saved = folder / "inputs.json"
        if o.resume and saved.exists():
            return dict(json.loads(saved.read_text()))
        if o.inputs:
            data: dict[str, Any] = json.loads(o.inputs.read_text())
        else:
            data = fetch_inputs(
                self.tools,
                self.cfg,
                o.today or self.now().astimezone(self._tz()).date(),
                self.data_dir.parent / "performance",
            )
        saved.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        if o.save_inputs:
            o.save_inputs.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(saved, o.save_inputs)
        return data

    def _tz(self) -> Any:
        from zoneinfo import ZoneInfo

        return ZoneInfo(self.cfg.slots.timezone)

    def _ideas(self, inputs: dict[str, Any]) -> list[slots.Idea]:
        state = self.state
        if state.data["ideas"]:  # a resumed run keeps its plan, dates included
            return [slots.idea_from_dict(r["idea"]) for r in state.data["ideas"].values()]
        today = dt.date.fromisoformat(inputs["today"])
        days = slots.plan_days(self.cfg, today)
        items = slots.probes(self.cfg, days)
        scores = slots.read_scores(items, inputs.get("probe_results", []))
        ideas, dropped = slots.assign(self.cfg, days, scores, self.opts.suffix)
        state.set(today=inputs["today"], dropped=[d.as_dict() for d in dropped])
        for idea in ideas:
            state.add_idea(idea.idea_id, idea=slots.idea_to_dict(idea))
        return ideas

    def _skip_existing(self, ideas: list[slots.Idea]) -> None:
        """A post cannot change under its key, and a re-run would write a different one: skip what already exists."""
        assert self.publisher is not None
        for idea in ideas:
            if self.state.held_whole(idea.idea_id):
                continue
            if self.publisher.post(idea.job_id):
                self.state.hold(idea.idea_id, f"already handed over as {idea.job_id} (to redo a rejected one, run with "
                                "--suffix r2)", code="exists")  # fmt: skip

    # ---------- production ----------

    def _produce(self, crew: Crew, ideas: list[slots.Idea], folder: Path) -> None:
        state, cfg = self.state, self.cfg
        todo_render: queue.Queue[str | None] = queue.Queue()
        todo_upload: queue.Queue[str | None] = queue.Queue()
        self.prep_lock = threading.Lock()
        self.planned_assets: list[str] = []
        uploads = self.publisher is not None and self.opts.publish != "render"
        render_t = threading.Thread(target=self._render_worker, args=(todo_render, todo_upload, folder), daemon=True)
        upload_t = threading.Thread(target=self._upload_worker, args=(todo_upload, folder), daemon=True)
        render_t.start()
        upload_t.start()
        for idea in ideas:  # resume: whatever was prepared but not rendered goes straight to the queue
            if state.held_whole(idea.idea_id):
                continue
            if state.reached(idea.idea_id, "prepared") and not state.reached(idea.idea_id, "rendered"):
                todo_render.put(idea.idea_id)
            elif state.reached(idea.idea_id, "rendered") and uploads:
                todo_upload.put(idea.idea_id)

        def work(idea: slots.Idea) -> None:
            try:
                crew.run(idea)
                designed = state.reached(idea.idea_id, "designed") and not state.reached(idea.idea_id, "prepared")
                if designed and self._prepare(idea):
                    todo_render.put(idea.idea_id)
            except Exception as ex:  # noqa: BLE001 - one idea failing never stops the others
                state.hold(idea.idea_id, f"unexpected error: {short(str(ex), 200)}", code="error")
                self.log.emit(idea.idea_id, "end", status="failed", error=short(repr(ex), 300))

        pending = [i for i in ideas if not state.reached(i.idea_id, "prepared")]
        try:
            with ThreadPoolExecutor(max_workers=cfg.budgets.parallel_ideas) as pool:
                list(pool.map(work, pending))
        finally:  # even when the run is stopped: the workers finish what is queued and end, never linger
            todo_render.put(None)
            render_t.join()
            todo_upload.put(None)
            upload_t.join()

    def _prepare(self, idea: slots.Idea) -> bool:
        """Code: the designer's answer becomes a checked spec. Returns True when the idea may go to the render queue.
        A spec the renderer refuses, or on-screen words the brand check blocks, go back to the designer ONCE with
        exactly what was wrong (the same repair round the captions get); a second failure holds the idea."""
        failure = self._try_prepare(idea)
        if failure is None:
            return True
        code, why, problems = failure
        if code == "no_spec":
            self.state.hold(idea.idea_id, why, code=code)
            return False
        design = self.state.idea(idea.idea_id).get("design") or {}
        repair = {"previous_spec": design.get("spec"), "problems": problems}
        if self.crew.design(idea, repair=repair):
            failure = self._try_prepare(idea)
            if failure is None:
                return True
            code, why, _ = failure
        self.state.hold(idea.idea_id, why, code=code)
        return False

    def _try_prepare(self, idea: slots.Idea) -> tuple[str, str, list[str]] | None:
        """None when prepared, else (hold code, reason, the problems in the designer's own words)."""
        state, i = self.state, idea.idea_id
        st = state.idea(i)
        platforms = [p for p in idea.slots if p in st.get("captions", {}) or p in st.get("options", {})]
        with self.prep_lock:  # the footage avoid-list grows idea by idea, so ideas don't share clips
            avoid = self.aledger.recent() + [a for a in self.planned_assets if a not in self.aledger.recent()]
            spec, why = media.finalize_idea_spec(
                st.get("design"), jid=idea.job_id, idea_format=idea.format,
                slots={p: idea.slots[p] for p in platforms}, captions=st.get("captions", {}),
                options=st.get("options", {}), avoid=avoid, week_label=idea.job_id.split("-")[0],
                prices=self.inputs.get("prices", []),
            )  # fmt: skip
            if spec is None:
                return "no_spec", why, [why]
            path = self.log.dir / f"{idea.job_id}.spec.json"
            path.write_text(json.dumps(spec, ensure_ascii=False, indent=1))
            code, out = self.renderer.dry_run(path)
            if code != 0:
                found = [f"{x['path']}: {x['message']}" for x in out.get("issues", [])[:3]] or [str(out.get("error"))]
                return "spec_refused", f"the designer's spec was refused: {short('; '.join(found), 200)}", found
            self.planned_assets += [
                a for a in media.assets_used({"plan": out.get("plan")}) if a not in self.planned_assets
            ]
        texts = [t for t in out.get("texts", []) if not t["where"].startswith("captions.")]
        bad = checks.check_texts(self.tools, self.cfg.plan.primary_platform, {idea.job_id: texts})[idea.job_id]
        if bad:
            return "text_on_screen", "on-screen text failed the brand check: " + "; ".join(bad[:3]), bad
        state.update(i, stage="prepared", spec_path=str(path))
        return None

    def _render_worker(self, todo: queue.Queue[str | None], upload: queue.Queue[str | None], folder: Path) -> None:
        state = self.state
        while (i := todo.get()) is not None:
            try:
                row = state.idea(i)
                idea = slots.idea_from_dict(row["idea"])
                self.log.emit(i, "render", status="start")
                t0 = time.monotonic()
                code, out = self.renderer.render(Path(row["spec_path"]))
                took = round(time.monotonic() - t0, 1)
                if out.get("manifest") and code in (0, 4):
                    manifest = json.loads(Path(out["manifest"]).read_text())
                    if code == 4:
                        failed = ", ".join(q["check"] for q in manifest["qa"] if not q["ok"])
                        state.hold(i, f"the render failed its quality checks: {failed}", code="qa_failed")
                    else:
                        thumb = folder / f"{idea.job_id}.jpg"
                        self.aledger.record(idea.job_id, media.assets_used(manifest))
                        state.update(i, stage="rendered", manifest=out["manifest"], render_seconds=took,
                                     thumb=thumb.name if self.renderer.thumbnail(manifest, Path(out["manifest"]).parent,
                                                                                 thumb) else None)  # fmt: skip
                        upload.put(i)
                else:
                    state.hold(i, f"the render failed: {short(str(out.get('error')), 160)}", code="render_failed")
                self.log.emit(i, "render", status="end", code=code, seconds=took)
            except Exception as ex:  # noqa: BLE001 - one render failing never stops the queue
                state.hold(i, f"the render crashed: {short(str(ex), 160)}", code="render_failed")

    def _upload_worker(self, todo: queue.Queue[str | None], folder: Path) -> None:
        """Uploads each master as soon as it is rendered, so the slow line works while the next render runs."""
        while (i := todo.get()) is not None:
            if self.publisher is None or self.opts.publish == "render":
                continue
            try:
                row = self.state.idea(i)
                manifest = json.loads(Path(row["manifest"]).read_text())
                t0 = time.monotonic()
                done = handover.warm_uploads(self.publisher, manifest, Path(row["manifest"]).parent,  # type: ignore[arg-type]
                                             self.publish_spec, self.hcfg, self.mledger)  # fmt: skip
                self.log.emit(i, "upload", files=len(done), seconds=round(time.monotonic() - t0, 1))
            except Exception as ex:  # noqa: BLE001 - the final handover uploads whatever is missing, once more
                self.log.emit(i, "upload", status="failed", error=short(str(ex), 200))

    # ---------- handover ----------

    def _handover(self, ideas: list[slots.Idea], folder: Path, publish_spec: dict[str, Any]) -> None:
        state, o = self.state, self.opts
        ready = [i.idea_id for i in ideas if state.reached(i.idea_id, "rendered") and not state.held_whole(i.idea_id)]
        if not ready:
            return
        if o.publish == "render" or self.publisher is None:
            for i in ready:
                state.update(i, stage="final", outcome={"state": "rendered", "platforms": self._ok_platforms(i)})
            return
        jobs = []
        for i in ready:
            manifest_path = Path(state.idea(i)["manifest"])
            jobs.append((json.loads(manifest_path.read_text()), manifest_path.parent))
        by_job = {slots.idea_from_dict(state.idea(i)["idea"]).job_id: i for i in ready}
        try:
            report = handover.submit(self.publisher, jobs, publish_spec, self.hcfg, self.mledger,  # type: ignore[arg-type]
                                     dry_run=o.publish == "dry-run")  # fmt: skip
        except handover.PublishError as ex:  # every idea it affected is named, never dropped silently
            for i in ready:
                state.hold(i, f"the handover failed: {short(str(ex), 200)}; the renders are kept: resume the run",
                           code="handover_failed")  # fmt: skip
            return
        self.report = report.as_dict()
        for h in report.held:
            if idea_id := by_job.get(h.job_id):
                state.hold(idea_id, h.reason, platform=h.platform, code=h.code)
        for f in report.failed:
            if idea_id := by_job.get(f["post_key"]):
                state.hold(
                    idea_id, "the publisher refused it: " + short(json.dumps(f["problems"]), 200), code="refused"
                )
        for label, keys in (("valid", report.would_submit), ("drafted", report.submitted),
                            ("replayed", report.replayed)):  # fmt: skip
            for k in keys:
                state.update(by_job[k], stage="final",
                             outcome={"state": label, "platforms": report.variants.get(k, [])})  # fmt: skip
        for idea_id in ready:  # held for one platform only: the others still went out
            if state.idea(idea_id)["stage"] != "final":
                state.update(idea_id, stage="final")

    def _ok_platforms(self, idea_id: str) -> list[str]:
        st = self.state.idea(idea_id)
        return [
            p
            for p in slots.idea_from_dict(st["idea"]).slots
            if p in st.get("captions", {}) or p in st.get("options", {})
        ]

    # ---------- report ----------

    def _finish(self, ideas: list[slots.Idea], folder: Path, run_id: str) -> int:
        state = self.state
        by_agent: dict[str, dict[str, int]] = {}
        for c in self.ai.calls:
            a = by_agent.setdefault(c["agent"], {"calls": 0, "tokens": 0, "failed": 0})
            a["calls"] += 1
            a["tokens"] += c["tokens"]
            a["failed"] += 0 if c["ok"] else 1
        held_codes: Counter[str] = Counter()
        variants_ok = 0
        planned_variants = 0
        done = 0
        rows = []
        for idea in ideas:
            st = state.idea(idea.idea_id)
            planned_variants += len(idea.slots)
            outcome = st.get("outcome")
            if outcome:
                done += 1
                variants_ok += len(outcome["platforms"])
            for platform, h in st["holds"].items():
                held_codes[h["code"]] += 1 if platform == "*" else 1
            rows.append({"idea_id": idea.idea_id, "date": idea.date.isoformat(), "format": idea.format,
                         "job_id": idea.job_id, "angle": st.get("angle"), "pillar": st.get("pillar"),
                         "outcome": outcome, "holds": st["holds"]})  # fmt: skip
        tokens = self.budget.used
        total_s = round(time.monotonic() - self.t0)
        summary = {
            "run_id": run_id, "publish": self.opts.publish, "config": self.cfg.digest(),
            "ideas": {"planned": len(ideas), "dropped_by_scorer": len(state.data["dropped"]), "completed": done,
                      "held": len(ideas) - done},
            "variants": {"planned": planned_variants, "made": variants_ok, "held_reasons": dict(held_codes)},
            "tokens": {"total": tokens, "cap": self.budget.max_tokens, "by_agent": by_agent,
                       "per_idea": round(tokens / max(1, len(ideas))),
                       "per_post": round(tokens / variants_ok) if variants_ok else None},
            "seconds": {"total": total_s, "phases": self.phase_seconds},
            "pillars": dict(Counter((state.idea(i.idea_id).get("pillar") or "?") for i in ideas)),
            "handover": getattr(self, "report", None),
        }  # fmt: skip
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        (folder / "result.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        (folder / "report.md").write_text(_report_md(run_id, self.opts, summary, rows, state.data["dropped"]))
        self.log.emit("orchestrator", "end", status="done", ideas=len(ideas), completed=done, variants=variants_ok,
                      tokens=tokens, seconds=total_s)  # fmt: skip
        return 0


def _report_md(
    run_id: str, o: Options, s: dict[str, Any], rows: list[dict[str, Any]], dropped: list[dict[str, Any]]
) -> str:
    v, t = s["variants"], s["tokens"]
    phases = ", ".join(f"{k} {x:.0f}s" for k, x in s["seconds"]["phases"].items())
    out = [f"# Studio run {run_id} ({o.trigger}, {o.publish})", "",
           f"Ideas: {s['ideas']['completed']} of {s['ideas']['planned']} made, "
           f"{s['ideas']['dropped_by_scorer']} slots ruled out by the scorer. "
           f"Posts: {v['made']} of {v['planned']} planned.",
           f"Tokens: {t['total']:,} of {t['cap']:,} ({t['per_idea']:,} per idea). Time: {s['seconds']['total']} s "
           f"({phases}).", ""]  # fmt: skip
    if v["held_reasons"]:
        out += ["Held, by reason: " + ", ".join(f"{k} {n}" for k, n in sorted(v["held_reasons"].items())), ""]
    for r in rows:
        o_ = r["outcome"]
        head = f"- {r['idea_id']} {r['date']} {r['format']} ({r['pillar'] or '?'}): "
        out.append(head + (f"{o_['state']} on {', '.join(o_['platforms'])}" if o_ else "not made"))
        out += [f"    held {p if p != '*' else 'whole idea'}: {h['why']}" for p, h in r["holds"].items()]
    out += [f"- {d['slot_id']} {d['date']}: ruled out ({d['why']})" for d in dropped]
    return "\n".join(out) + "\n"
