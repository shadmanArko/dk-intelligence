"""Plug-in: the two post scorers (engagement question -> content_scorer, reach question -> reach_scorer)."""

from __future__ import annotations

import json
from typing import Any

from intel.plugins import Box, ModelCard, ModelPlugin, RunContext, StepOutcome


def _sources(spec: str) -> tuple[str, ...]:
    return (
        "models/content_scorer/features.py",
        "models/content_scorer/predict.py",
        spec,
        "champions/harness_v1/features.py",
    )


def _scores(t: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: t.get(k) for k in ("balanced_accuracy", "roc_auc", "brier", "n")} if t else None


class ContentScorer(ModelPlugin):
    step = "train"
    order = 10
    label = "Train candidate models"
    desc = (
        "Two questions, one model each: content_scorer (more likes + comments + shares than usual?) and reach_scorer "
        "(more people reached than our last 20 posts?). Features use only what is known before posting; several "
        "candidates per question are trained on all posts except the newest 20% and chosen by time-ordered "
        "cross-validation."
    )
    inputs = ("market", "pull", "ledger")
    box = Box(col=2, row=1, label="Train candidates")
    kind = "scorer"
    cards = {
        "content_scorer": ModelCard(
            intended_use="Before posting: estimate whether a planned Facebook / Instagram post will get more likes, "
            "comments and shares than our usual post on that platform, to choose between drafts and posting times.",
            out_of_scope=(
                "Judging other accounts or competitors' posts (trained on our own account only).",
                "Platforms it never saw in training (Threads, YouTube, TikTok): their scores mean nothing.",
                "Paid / boosted posts (their reach is bought, not earned).",
            ),
            limitations=(
                "Trained on a few hundred posts: moderate accuracy, scores bunch around 0.5; read it as a tie-breaker, "
                "not a verdict.",
                "Sees only format, timing and caption features; not the image or video itself.",
                "Labels mature after 7 days, so live accuracy lags a week (drift is the early signal).",
            ),
        ),
        "reach_scorer": ModelCard(
            intended_use="Before posting: estimate whether a planned post will reach more people than our last 20 "
            "posts on the same platform.",
            out_of_scope=(
                "Comparing reach across platforms (each platform has its own baseline).",
                "Paid / boosted posts.",
            ),
            limitations=(
                "Reach depends on platform algorithms that change without notice; monitored for drift.",
                "Sees only format, timing and caption features; not the creative itself.",
            ),
        ),
    }
    models = {
        "content_scorer": _sources("models/content_scorer/spec.yaml"),
        "reach_scorer": _sources("models/reach_scorer/spec.yaml"),
    }

    def run(self, ctx: RunContext) -> StepOutcome:
        from intel.models.content_scorer.run import TARGETS
        from intel.models.content_scorer.run import run as train_run

        ctx.log("training and judging")
        reports = []
        for target in TARGETS:  # engagement first: its numbers stay the run report's headline
            d = train_run(
                ctx.pull, ctx.market, ctx.runs_dir, ctx.mlflow_uri, log=ctx.log, ledger=ctx.ledger, target=target
            )
            reports.append((d, json.loads((d / "report.json").read_text())))
        r = reports[0][1]
        j = r["judge"]
        details: dict[str, Any] = {
            "training posts": r["split"]["train"],
            "test posts (newest, unseen)": r["split"]["test"],
            "test period from": r["split"]["cutoff"][:10],
        }
        for _, rr in reports:
            name = rr["kind"].removesuffix("_run")
            details[f"{name}: chosen"] = f"{rr['challenger']['model']} {rr['challenger']['params']}"
            details[f"{name}: candidates (CV)"] = (
                ", ".join(f"{c['name']} {c['cv_balanced_accuracy@0.5']}" for c in rr.get("leaderboard_cv", [])) or "–"
            )
        headline = {
            "version": r["version"],
            "posts": r["split"]["train"] + r["split"]["test"],
            "challenger": _scores(r["challenger"]["test"]),
            "champion": _scores(r["champion"]["test"]),
            "gain": j.get("gain"),
            "failed_gates": j.get("failed", []),
            "live": next((g["detail"] for g in j["gates"] if g["gate"] == "live_evidence"), None),
        }
        return StepOutcome([d for d, _ in reports], f"{len(reports)} models trained", details, headline)

    def scores(self, report: dict[str, Any]) -> tuple[float | None, float | None]:
        return report["challenger"]["test"]["balanced_accuracy"], report["champion"]["test"]["balanced_accuracy"]


PLUGIN = ContentScorer()
