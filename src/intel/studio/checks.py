"""The studio's gates, in code, outside the AI (A12): every word that ships goes through dk_intel, the one owner of the
brand rules. Captions are judged as captions (errors AND warnings fail); a short on-screen line or a title is judged on
blocking errors only (caption-style warnings such as 'no Kacchi in this line' are noise there)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intel.studio.config import StudioConfig
from intel.studio.inputs import evaluate
from intel.studio.ports import ToolPort
from intel.studio.slots import SCORABLE, Idea


@dataclass
class Verdict:
    ok: bool
    codes: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    score: float | None = None
    below_bar: bool = False  # the scorer expects below-usual engagement for this slot; the words are fine


def _codes(items: Any) -> list[str]:
    return [str(e.get("code") or e.get("rule") or e) if isinstance(e, dict) else str(e) for e in items or []]


def verdict_of(r: dict[str, Any], strict: bool = True) -> Verdict:
    errors, warnings = _codes(r.get("errors")), _codes(r.get("warnings"))
    codes = errors + (warnings if strict else [])
    prob = ((r.get("scores") or {}).get("content_scorer") or {}).get("probability")
    below = r.get("verdict") == "rewrite" and not errors and not warnings
    ok = r.get("verdict") != "blocked" and not codes
    return Verdict(ok, codes or (["BLOCKED"] if r.get("verdict") == "blocked" else []), list(r.get("fixes") or []),
                   float(prob) if isinstance(prob, int | float) else None, below)  # fmt: skip


def check_variants(
    tools: ToolPort,
    cfg: StudioConfig,
    idea: Idea,
    captions: dict[str, str],
    options: dict[str, dict[str, Any]],
    only: list[str] | None = None,
) -> dict[str, Verdict]:
    """One dk_intel call for all of an idea's platform texts. Returns a verdict per platform; a platform with several
    texts (YouTube: title + description) passes when all of them do."""
    content_type = cfg.routes.formats[idea.format].content_type
    drafts: list[dict[str, Any]] = []
    owner: dict[str, tuple[str, bool]] = {}
    for platform in idea.slots:
        if only is not None and platform not in only:
            continue
        pieces: list[tuple[str, str, bool]] = []  # (ref suffix, text, strict)
        if captions.get(platform):
            pieces.append(("c", captions[platform], True))
        opts = options.get(platform) or {}
        if opts.get("title"):
            pieces.append(("t", str(opts["title"]), False))
        if opts.get("description"):
            pieces.append(("d", str(opts["description"]), True))
        for suffix, text, strict in pieces:
            ref = f"{idea.idea_id}.{platform[:3]}.{suffix}"
            draft: dict[str, Any] = {"ref": ref, "platform": platform, "caption": text}
            if platform in SCORABLE:
                draft.update(content_type=content_type, planned_posted_at=idea.slots[platform])
            drafts.append(draft)
            owner[ref] = (platform, strict)
    got = {r.get("ref"): r for r in evaluate(tools, drafts)}
    out: dict[str, Verdict] = {}
    for platform in idea.slots:
        if only is not None and platform not in only:
            continue
        mine = [(ref, strict) for ref, (p, strict) in owner.items() if p == platform]
        if not mine:
            out[platform] = Verdict(
                False, ["NO_TEXT"], ["write the caption (or the title and description) for this platform"]
            )
            continue
        parts = [
            verdict_of(got[ref], strict) if ref in got else Verdict(False, ["NOT_CHECKED"]) for ref, strict in mine
        ]
        out[platform] = Verdict(all(v.ok for v in parts), [c for v in parts for c in v.codes],
                                [f for v in parts for f in v.fixes][:3],
                                next((v.score for v in parts if v.score is not None), None),
                                any(v.below_bar for v in parts))  # fmt: skip
    return out


def check_texts(tools: ToolPort, platform: str, texts: dict[str, list[dict[str, str]]]) -> dict[str, list[str]]:
    """Every word a viewer reads on screen. Returns job_id → the problems found (empty when clean). The renderer holds
    no brand rules: dk_intel does. Chunked by 20 however many posts are checked at once."""
    drafts: list[dict[str, str]] = []
    owner: dict[str, tuple[str, str]] = {}
    for jid, items in texts.items():
        for t in items:
            ref = f"t{len(drafts)}"
            owner[ref] = (jid, t["where"])
            drafts.append({"ref": ref, "platform": platform, "caption": t["text"]})
    problems: dict[str, list[str]] = {jid: [] for jid in texts}
    for r in evaluate(tools, drafts):
        codes = _codes(r.get("errors"))
        if codes or r.get("verdict") == "blocked":
            jid, where = owner[r["ref"]]
            problems[jid].append(f"{where}: {', '.join(codes) or 'blocked'}")
    return problems
