"""Plug-in: competitor weakness miner (aspects praised / complained about in biryani reviews)."""

from __future__ import annotations

import json
from html import escape
from typing import Any

from intel.plugins import Box, ModelPlugin, RunContext, StepOutcome


class WeaknessMiner(ModelPlugin):
    step = "mine"
    order = 20
    label = "Mine competitor reviews"
    desc = (
        "Reads ~13.7k Lieferando reviews of the Berlin places that sell biryani, splits them into clauses and finds "
        "14 topics (delivery time, rice, meat, portion, wrong items, ...) with praise or complaint, per competitor "
        "and district. Every decision is a readable dictionary phrase; your 200-sentence check measures accuracy."
    )
    inputs = ("market",)
    box = Box(col=2, row=0, label="Review miner", sub="competitor weaknesses")
    kind = "insights"
    models = {
        "weakness_miner": (
            "models/weakness_miner/spec.yaml",
            "models/weakness_miner/aspects.yaml",
            "models/weakness_miner/mine.py",
        )
    }

    def run(self, ctx: RunContext) -> StepOutcome:
        from intel.models.weakness_miner.run import run as mine_run

        ctx.log("mining competitor reviews")
        d = mine_run(ctx.market, ctx.runs_dir, ctx.checks_dir, log=ctx.log)
        r = json.loads((d / "report.json").read_text())
        ins = json.loads((d / "insights.json").read_text())
        return StepOutcome(
            [d],
            f"{ins['reviews']:,} reviews, top complaint: {ins['overall'][0]['label']}",
            {
                "reviews": ins["reviews"],
                "places": ins["listings"],
                "clauses": ins["clauses"],
                "clauses with a topic": ins["clauses_with_aspect"],
                **{
                    f"#{i + 1} complaint": f"{a['label']} ({a['negative']}, {a['negative_rate']:.0%} negative)"
                    for i, a in enumerate(ins["overall"][:5])
                },
                "your check": f"{r['check']['checked']} sentences checked",
            },
        )

    def scores(self, report: dict[str, Any]) -> tuple[float | None, float | None]:
        return report["check"]["aspect_precision"], None

    def approval_text(self, meta: dict[str, Any], approval_id: int, public_url: str | None) -> str:
        c = meta.get("check") or {}
        tops = ", ".join(meta.get("top_complaints") or []) or "–"
        link = f'\n\n<a href="{escape(public_url)}/packages/{approval_id}">Open details</a>' if public_url else ""
        return (
            f"🔎 <b>New insights passed the checks</b>\n\n<b>{escape(meta['name'])}</b> "
            f"<code>{escape(meta['version'])}</code>\n"
            f"Your check: aspect precision {c.get('aspect_precision')}, recall {c.get('aspect_recall')}, "
            f"tone accuracy {c.get('polarity_accuracy')} on {c.get('checked')} sentences\n"
            f"Top complaints: {escape(tops)}\nApproving shows them on the Insights page.{link}"
        )


PLUGIN = WeaknessMiner()
