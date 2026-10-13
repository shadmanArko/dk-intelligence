"""One idea's crew: copywriter (the core message) → adapter (a native version per platform) → designer (one master).

The agents run one after the other for an idea; ideas run side by side (`parallel_ideas`). Between them code does every
fixed step: the brand check of every platform text, the one repair round, the price rows, the spec envelope.
Everything an idea earns is written to the run state at once, so a stopped run resumes without paying twice.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from intel.studio import checks, director, media, requests
from intel.studio.ai import AI
from intel.studio.config import StudioConfig
from intel.studio.inputs import platform_slices
from intel.studio.plan import cites
from intel.studio.ports import ToolPort
from intel.studio.runlog import RunLog
from intel.studio.slots import Idea
from intel.studio.state import RunState


class Crew:
    def __init__(self, ai: AI, tools: ToolPort, cfg: StudioConfig, state: RunState, log: RunLog,
                 inputs: dict[str, Any], publish_spec: dict[str, Any]) -> None:  # fmt: skip
        self.ai, self.tools, self.cfg, self.state, self.log = ai, tools, cfg, state, log
        self.inputs, self.publish_spec = inputs, publish_spec
        self.cites = cites(inputs["pack"])
        self.registry = director.template_registry(inputs["vocab"])
        self.reminders = [str(x) for x in inputs["pack"].get("brand_reminders", [])]

    def text_platforms(self, idea: Idea) -> list[str]:
        """The platforms of this idea that take words (a caption or title and description). An Instagram story takes
        none: nothing to write there, nothing to check."""
        slices = platform_slices(self.cfg, self.publish_spec, self.inputs.get("hashtags", {}), idea.format,
                                 list(idea.slots), None)  # fmt: skip
        return [p for p, s in slices.items() if s["caption_max"] is not None or s["option_fields"]]

    def run(self, idea: Idea) -> None:
        """All AI work for one idea. Each step is skipped when the state already has its result (resume)."""
        i = idea.idea_id
        if self.state.held_whole(i):
            return
        if not self.text_platforms(idea) and not self.state.reached(i, "adapted"):
            # e.g. an Instagram story: no caption anywhere, so no copywriter and no adapter, only the picture
            self.state.update(i, stage="adapted", core={"caption": "", "score": None}, captions={}, options={},
                              platforms_ok=list(idea.slots), evidence=[])  # fmt: skip
        if not self.state.reached(i, "written") and not self.write_core(idea):
            return
        if not self.state.reached(i, "adapted") and not self.adapt(idea):
            return
        if not self.state.reached(i, "designed"):
            self.design(idea)

    # ---------- 1. the core caption ----------

    def _fail(self, idea: Idea, res_error: str, what: str) -> None:
        code = "budget" if res_error.startswith("budget:") else "agent_failed"
        self.state.hold(idea.idea_id, f"{what}: {res_error or 'no answer'}", code=code)

    def write_core(self, idea: Idea) -> bool:
        st = self.state.idea(idea.idea_id)
        primary = idea.lead(self.cfg.plan.primary_platform)
        evidence = [{"cite": c, "text": self.cites[c]} for c in st.get("sources", []) if c in self.cites]
        brief = {
            "platform": primary, "date": idea.date.isoformat(), "planned_posted_at": idea.slots[primary],
            "content_type": self.cfg.routes.formats[idea.format].content_type, "format": idea.format,
            "slot_id": idea.idea_id, "angle": st["angle"], "pillar": st.get("pillar", ""), "sources": st["sources"],
            "evidence": evidence,
            "examples": [{"cite": e["cite"], "text": e["text"], "vs_our_median": e["x"],
                          "format_ok": e.get("format_ok", True)} for e in self.inputs.get("examples", [])],
            "brand": self.reminders, "not_on_the_menu": self.inputs.get("unavailable", []),
        }  # fmt: skip
        res = self.ai.call(f"copywriter:{idea.idea_id}", "copywriter", brief, idea.idea_id,
                           lambda a: bool(str(a.get("caption") or "").strip()))  # fmt: skip
        a = res.answer or {}
        caption = str(a.get("caption") or "").strip()
        if res.answer is None or not caption:
            self._fail(idea, res.error or "no caption", "the copywriter failed")
            return False
        # a weak slot score is not a reason to drop a surface the owner wants in the mix (gate: false); the words are
        # re-checked per platform below either way, so a 'rewrite' caused by wording still cannot go out
        scored_only = not self.cfg.routes.formats[idea.format].gate and a.get("verdict") == "rewrite"
        if a.get("verdict") != "keep" and not scored_only:
            why = f"the copywriter's best caption was '{a.get('verdict')}' (score {a.get('score')})"
            self.state.hold(idea.idea_id, why, code="caption_not_kept")
            return False
        self.state.update(idea.idea_id, stage="written", core={"caption": caption, "score": a.get("score")},
                          evidence=evidence)  # fmt: skip
        return True

    # ---------- 2. a native version per platform ----------

    def _sanitise(self, answer: dict[str, Any] | None, idea: Idea) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
        """The adapter's answer is data: keep only texts for platforms of this idea and only the options it may write,
        clipped to the publisher's limits. Anything else is dropped here; privacy and kids settings are code's."""
        captions: dict[str, str] = {}
        options: dict[str, dict[str, Any]] = {}
        a = answer or {}
        for p, text in (a.get("captions") or {}).items():
            if p in idea.slots and isinstance(text, str) and text.strip():
                captions[p] = text.strip()
        for p, opts in (a.get("options") or {}).items():
            if p not in idea.slots or not isinstance(opts, dict):
                continue
            allowed = self.cfg.routes.adapter_options.get(p, [])
            fmt = self.cfg.routes.formats[idea.format].targets[p]
            rules = ((self.publish_spec.get("platforms") or {}).get(p) or {}).get("formats", {}).get(fmt, {})
            keep: dict[str, Any] = {}
            for k, v in opts.items():
                if k in allowed and isinstance(v, str) and v.strip():
                    limit = (rules.get("options", {}).get(k) or {}).get("max_length")
                    keep[k] = v.strip()[:limit] if limit else v.strip()
            if keep:
                options[p] = keep
        return captions, options

    def adapt(self, idea: Idea) -> bool:
        i = idea.idea_id
        st = self.state.idea(i)
        platforms = self.text_platforms(idea)
        slices = platform_slices(
            self.cfg,
            self.publish_spec,
            self.inputs.get("hashtags", {}),
            idea.format,
            platforms,
            self.inputs.get("performance"),
        )
        task: dict[str, Any] = {
            "idea_id": i,
            "idea": {"angle": st["angle"], "pillar": st.get("pillar", ""), "core_caption": st["core"]["caption"],
                     "evidence": st.get("evidence", [])},
            "platforms": slices, "brand": self.reminders, "not_on_the_menu": self.inputs.get("unavailable", []),
        }  # fmt: skip
        res = self.ai.call(f"adapter:{i}", "adapter", task, i, lambda a: isinstance(a.get("captions"), dict))
        if res.answer is None:
            self._fail(idea, res.error, "the platform adapter failed")
            return False
        captions, options = self._sanitise(res.answer, idea)
        verdicts = checks.check_variants(self.tools, self.cfg, idea, captions, options, only=platforms)
        failing = [p for p, v in verdicts.items() if not v.ok]
        if failing:  # one repair round, only for the platforms that failed, with exactly what the checker said
            repair = {p: {"caption": captions.get(p), "options": options.get(p), "problems": verdicts[p].codes,
                          "fixes": verdicts[p].fixes} for p in failing}  # fmt: skip
            res2 = self.ai.call(f"adapter-repair:{i}", "adapter", {**task, "repair": repair}, i)
            if res2.answer is not None:
                c2, o2 = self._sanitise(res2.answer, idea)
                for p in failing:
                    if p in c2:
                        captions[p] = c2[p]
                    if p in o2:
                        options[p] = o2[p]
                verdicts.update(checks.check_variants(self.tools, self.cfg, idea, captions, options, only=failing))
        good = [p for p, v in verdicts.items() if v.ok]
        for p, v in verdicts.items():
            if not v.ok:
                self.state.hold(i, "the " + p + " text failed the brand check: " + ", ".join(v.codes[:3]), platform=p,
                                code="text_failed")  # fmt: skip
        if not good:
            self.state.hold(i, "no platform text passed the brand check", code="text_failed")
            return False
        textless = [p for p in idea.slots if p not in platforms]  # platforms that take no words are fine as they are
        self.state.update(
            i, stage="adapted", platforms_ok=good + textless, captions={p: captions[p] for p in good if p in captions},
            options={p: o for p, o in options.items() if p in good},
            text_checks={p: {"score": verdicts[p].score, "below_bar": verdicts[p].below_bar} for p in good},
        )  # fmt: skip
        return True

    # ---------- 3. one master ----------

    def design(self, idea: Idea, repair: dict[str, Any] | None = None) -> bool:
        i = idea.idea_id
        st = self.state.idea(i)
        platforms = [p for p in idea.slots if p in self.platforms_ok(i)]
        task = media.idea_designer_task(
            jid=idea.job_id, idea_format=idea.format, platforms=platforms, angle=st["angle"],
            caption=st["core"]["caption"], evidence=st.get("evidence", []), brand=self.reminders,
            vocab=self.inputs["vocab"], inventory=self.inputs["inventory"],
        )  # fmt: skip
        task["not_on_the_menu"] = self.inputs.get("unavailable", [])
        task.update(self._treatment_for_designer(idea))
        if repair:  # a second try: the spec it made was refused, here is exactly what was wrong
            task["repair"] = repair
        res = self.ai.call(f"designer{'-repair' if repair else ''}:{i}", "designer", task, i,
                           lambda a: isinstance(a.get("spec"), dict))  # fmt: skip
        if res.answer is None:
            self._fail(idea, res.error, "the designer failed")
            return False
        self.state.update(i, stage="designed", design=res.answer)
        if str(res.answer.get("style_request") or "").strip():
            requests.add(i, "designer", str(res.answer["style_request"]), idea.format)
        return True

    def _treatment_for_designer(self, idea: Idea) -> dict[str, Any]:
        """What the creative director decided, with the registry's own instructions for that template."""
        t = self.state.idea(idea.idea_id).get("treatment")
        if not t:
            return {}
        meta = self.registry.get(t["template"], {})
        out: dict[str, Any] = {"template": t["template"], "variant": t.get("variant") or None,
                               "music_hint": t.get("music"),
                               "pace": t.get("pace"), "template_brief": meta.get("designer_brief", ""),
                               "why": t.get("why")}  # fmt: skip
        if "photos" in str(meta.get("needs", "")):
            out["photo_categories"] = (self.inputs.get("inventory") or {}).get("photo", {})
        example = meta.get("example")
        if example:
            from intel.studio import media

            path = media.REELS / str(example)
            with contextlib.suppress(OSError, ValueError):  # no example on this machine: the brief alone
                out["example_spec"] = json.loads(path.read_text())
        return {k: v for k, v in out.items() if v not in (None, "", {})}

    def platforms_ok(self, idea_id: str) -> set[str]:
        st = self.state.idea(idea_id)
        if "platforms_ok" in st:
            return set(st["platforms_ok"])
        return set(st.get("captions", {})) | set(st.get("options", {}))
