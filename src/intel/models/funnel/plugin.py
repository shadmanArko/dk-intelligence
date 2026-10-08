"""Plug-in: visit-to-order funnel and attribution (counts only; no visitor data leaves the step)."""

from __future__ import annotations

import json
from typing import Any

from intel.plugins import Box, ModelCard, ModelPlugin, RunContext, StepOutcome, insights_approval


class Funnel(ModelPlugin):
    step = "funnel"
    order = 50
    label = "Funnel and attribution"
    desc = (
        "From the website analytics: where visits come from (Facebook, Instagram, WhatsApp, Google, unknown), how far "
        "they get (menu, cart, checkout, order) and what stops them at checkout; orders matched to visits; visits "
        "after each post (an estimate until every post carries a tracked link). Only counts leave this step."
    )
    inputs = ("pull",)
    box = Box(col=2, row=2, label="Funnel", sub="visits → orders")
    kind = "insights"
    cards = {
        "funnel": ModelCard(
            intended_use="Show where website visits come from and where visitors drop off before ordering, to fix "
            "the checkout and choose channels.",
            out_of_scope=("Tracking or identifying individual visitors: only counts leave this step.",),
            limitations=(
                "Most visits arrive without a source until every post carries a tracked link.",
                "Very few orders so far: conversion rates are not yet stable.",
            ),
        ),
    }
    models = {"funnel": ("models/funnel/spec.yaml", "models/funnel/run.py")}

    def run(self, ctx: RunContext) -> StepOutcome:
        from intel.models.funnel.run import run as funnel_run

        d = funnel_run(ctx.pull, ctx.runs_dir, log=ctx.log)
        fi = json.loads((d / "insights.json").read_text())
        t = fi["totals"]
        return StepOutcome(
            [d],
            f"{t['visit']} visits → {t['ordered']} orders",
            {
                **{f"{k}": v for k, v in t.items()},
                "unknown source": f"{fi['unknown_share']:.0%}",
                "tracked links": fi["tracked_links"],
                "stuck at checkout": fi["checkout_friction"]["stuck_sessions"],
            },
        )

    def approval_text(self, meta: dict[str, Any], approval_id: int, public_url: str | None) -> str:
        return insights_approval(
            "🧭",
            "Visit-to-order funnel",
            meta,
            public_url,
            "Only counts, no visitor data. Approve once; later weekly versions then refresh automatically.",
        )


PLUGIN = Funnel()
