"""Plug-in: creator matcher (a shortlist of Berlin food creators for collaborations)."""

from __future__ import annotations

import json
from typing import Any

from intel.plugins import Box, ModelCard, ModelPlugin, RunContext, StepOutcome, insights_approval


class CreatorMatcher(ModelPlugin):
    step = "creators"
    order = 60
    label = "Creator matcher"
    desc = (
        "Ranks Berlin food creators on TikTok (865 accounts with Berlin posts; restaurants and brands set aside): "
        "Berlin focus, topic fit (rice dishes, biryani, South Asian / Middle Eastern food, halal, street food), "
        "engagement per view and activity, preferring nano and micro creators. A shortlist for collaborations."
    )
    inputs = ("market",)
    box = Box(col=3, row=2, label="Creator matcher", sub="Berlin food creators")
    kind = "insights"
    cards = {
        "creator_matcher": ModelCard(
            intended_use="Shortlist Berlin food creators whose topics and audiences fit Dhaka Kacchi, for "
            "collaborations the owner initiates.",
            out_of_scope=(
                "Contacting creators automatically, or judging people beyond their public posts.",
                "Any collaboration without a 'Werbung' (advertising) label.",
            ),
            limitations=(
                "TikTok does not expose follower counts; size is estimated from typical views.",
                "Only creators with public Berlin posts in the market data are seen.",
            ),
        ),
    }
    models = {"creator_matcher": ("models/creator_matcher/spec.yaml", "models/creator_matcher/run.py")}

    def run(self, ctx: RunContext) -> StepOutcome:
        from intel.models.creator_matcher.run import run as creators_run

        d = creators_run(ctx.market, ctx.runs_dir, log=ctx.log)
        ci = json.loads((d / "insights.json").read_text())
        return StepOutcome(
            [d],
            f"{ci['eligible']} creators ranked",
            {
                "candidates": ci["candidates"],
                "set aside as businesses / brands": ci["business_like"],
                **{f"#{i + 1}": f"@{c['handle']} ({c['size']})" for i, c in enumerate(ci["top"][:5])},
            },
        )

    def approval_text(self, meta: dict[str, Any], approval_id: int, public_url: str | None) -> str:
        return insights_approval(
            "🎥",
            "Creator shortlist",
            meta,
            public_url,
            "Do these look like real food creators to you? Approve once; later weekly versions refresh automatically.",
        )


PLUGIN = CreatorMatcher()
