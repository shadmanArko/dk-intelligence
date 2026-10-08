"""Plug-in: occasion planner (the next 30 days: which occasions, for whom, when to start posting)."""

from __future__ import annotations

import json
from typing import Any

from intel.plugins import Box, ModelPlugin, RunContext, StepOutcome, insights_approval


class OccasionPlanner(ModelPlugin):
    step = "occasions"
    order = 40
    label = "Occasion planner"
    desc = (
        "The next 30 days: which occasions matter to which Berlin audience (food role x people who celebrate it), when "
        "to start posting, greetings and rice-dish bridges, Berlin events for pop-ups, and the weekdays / times when "
        "our own posts reached most people. Content rules applied and checked automatically (no blocked words)."
    )
    inputs = ("market", "pull")
    box = Box(col=4, row=0, label="Occasion planner", sub="next 30 days")
    kind = "insights"
    models = {"occasion_planner": ("models/occasion_planner/spec.yaml", "models/occasion_planner/run.py")}

    def run(self, ctx: RunContext) -> StepOutcome:
        from intel.models.occasion_planner.run import run as plan_run

        d = plan_run(ctx.market, ctx.pull, ctx.runs_dir, log=ctx.log)
        r = json.loads((d / "report.json").read_text())
        op = json.loads((d / "insights.json").read_text())
        return StepOutcome(
            [d],
            f"{len(op['picks'])} occasions in the next 30 days",
            {
                **{
                    f"#{i + 1}": f"{p['label']} ({p['community_id']}, {p['people']:,}) on {p['first_day']}, start "
                    f"{p['start_posting']}"
                    for i, p in enumerate(op["picks"][:5])
                },
                "respectful notes": len(op["respectful_notes"]),
                "content rules": next(g["detail"] for g in r["judge"]["gates"] if g["gate"] == "content_rules"),
            },
        )

    def approval_text(self, meta: dict[str, Any], approval_id: int, public_url: str | None) -> str:
        return insights_approval(
            "📅",
            "Occasion plan, next 30 days",
            meta,
            public_url,
            "Content rules checked: no blocked words. Approve once; later weekly plans then refresh automatically.",
        )


PLUGIN = OccasionPlanner()
