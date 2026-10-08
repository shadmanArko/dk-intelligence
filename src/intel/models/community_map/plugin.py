"""Plug-in: community map (scores Berlin planning areas for delivery, ads and pop-ups)."""

from __future__ import annotations

import json
from html import escape
from typing import Any

from intel.plugins import Box, ModelPlugin, RunContext, StepOutcome


class CommunityMap(ModelPlugin):
    step = "community"
    order = 30
    label = "Community map"
    desc = (
        "Scores all 542 Berlin planning areas: residents of South Asian origin, meat-and-rice food cultures, "
        "international food explorers and young small households (public statistics), times a reach factor for the "
        "delivery bands from Leopoldplatz. Flags gaps: big audience, no biryani seller. Chooses AREAS, never people."
    )
    inputs = ("market", "pull")
    box = Box(col=3, row=0, label="Community map", sub="542 Berlin areas")
    kind = "insights"
    models = {"community_map": ("models/community_map/spec.yaml", "models/community_map/run.py")}

    def run(self, ctx: RunContext) -> StepOutcome:
        from intel.models.community_map.run import run as map_run

        d = map_run(ctx.market, ctx.pull, ctx.runs_dir, log=ctx.log)
        cm = json.loads((d / "insights.json").read_text())
        top = cm["areas"][0]
        return StepOutcome(
            [d],
            f"top area: {top['name']} ({top['district']})",
            {
                "areas scored": cm["n_areas"],
                "located": cm["located"],
                **{
                    f"#{i + 1}": f"{a['name']} ({a['district']}, {a['distance_km']} km){' · gap' if a['gap'] else ''}"
                    for i, a in enumerate(cm["areas"][:5])
                },
                "gaps (big audience, no biryani seller)": sum(1 for a in cm["areas"] if a["gap"]),
                "your orders located": cm["orders_located"],
            },
        )

    def approval_text(self, meta: dict[str, Any], approval_id: int, public_url: str | None) -> str:
        tops = "\n".join(f"{i + 1}. {escape(str(t))}" for i, t in enumerate(meta.get("top_complaints") or []))
        link = f'\n\n<a href="{escape(public_url)}/packages/{approval_id}">Open details</a>' if public_url else ""
        return (
            f"🗺 <b>Community map ready</b>\n\n<code>{escape(meta['version'])}</code>\nTop areas for delivery:\n"
            f"{tops}\n\nDo these look right to you? Approving shows the map on the Insights page.{link}"
        )


PLUGIN = CommunityMap()
