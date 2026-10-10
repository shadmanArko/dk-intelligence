"""The ONE description of the intelligence pipeline, used by:
- the Sunday run (`intel.weekly`: which steps exist and their order),
- the Control Room on the Mac and the Training page on the VPS (the diagram),
- Telegram /training.

A new MODEL needs nothing here: its plug-in (`intel.models.<name>.plugin`) brings its step, box and arrows, and
they are placed between the ledger download and the judge. To add a non-model step (a data source, a stage of the
run): add a CORE step / node / edges below.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from intel.plugins import ModelPlugin, discover

# Core steps the Sunday run executes. key -> (label, what it does). Model steps go between "ledger" and "judge".
CORE_STEPS: dict[str, tuple[str, str]] = {
    "wait": (
        "Wait for the data refresh",
        "The market data platform refreshes every Sunday at 01:00 on this Mac (Wolt, Lieferando, Reddit, YouTube, "
        "events, ...). Training waits until that has finished, so it always uses this week's market data.",
    ),
    "pull": (
        "Pull the warehouse",
        "Copies your business data from the VPS warehouse: posts, their metrics over time, website visits, Search "
        "Console, orders. Read-only database role, through an SSH tunnel over Tailscale that can only reach the "
        "database. Saved as a verified, immutable snapshot (Parquet + checksums), then checked against the data "
        "contracts of every table a model reads (columns, types, values, freshness, volume vs last week). A broken "
        "contract stops the run here: no model ever trains on it.",
    ),
    "market": (
        "Market snapshot",
        "Reads what the data platform learned from ~1.9M public posts: which topics, languages and formats "
        "perform, the occasion calendar per community, and the topic dictionary. Checked against its data "
        "contracts; a snapshot that fails falls back to the newest earlier one that passes.",
    ),
    "ledger": (
        "Download the live ledger",
        "Fetches from the VPS what each model predicted for every real post BEFORE its results existed, plus "
        "the real results after 24h / 72h / 7 days. Used to check new models against reality.",
    ),
    "judge": (
        "Judge against the current model",
        "For each question, seven automatic checks on the newest posts, never seen in training, against what we use "
        "today: data sanity, no leakage, clearly better, no platform worse, well calibrated, live evidence, works "
        "(size, speed). All must pass before a model is offered to you.",
    ),
    "package": (
        "Package the model",
        "Only if the judge passed: seals the model, its feature code, scores and model card into one checksummed file.",
    ),
    "upload": (
        "Upload to the VPS",
        "Sends the package to the VPS inbox (an upload-only door: cannot read or delete anything) and checks "
        "the bytes arrived exactly.",
    ),
    "knowledge": (
        "Rebuild the knowledge pack",
        "Rebuilds the search index the AI assistants use (competitor reviews, our posts, creator posts, the brand "
        "book, what lifts engagement) from this week's data. Only new text is embedded. The new pack goes live only "
        "if it passes its gate: no question it could answer last week is lost, and no personal data is left in it. "
        "Otherwise last week's stays. Never fails the model run.",
    ),
    "report": (
        "Report the result",
        "Uploads a short run report; the VPS turns it into a Telegram message (new model waiting, no "
        "improvement, or failed at step X).",
    ),
}

# Diagram. lane: source | mac | vps. col/row: grid position. step: which run step drives its colour (None = a
# service that is always there).
CORE_NODES: list[dict[str, Any]] = [
    {
        "id": "mip",
        "label": "Market data platform",
        "sub": "this Mac · Sun 01:00",
        "lane": "source",
        "col": 0,
        "row": 0,
        "step": "wait",
    },
    {
        "id": "warehouse",
        "label": "VPS warehouse",
        "sub": "orders · posts · web · search",
        "lane": "source",
        "col": 0,
        "row": 1,
        "step": None,
        "desc": "The Dhaka Kacchi warehouse on the VPS, filled hourly/daily by the harness (Instagram, Facebook, "
        "Threads, YouTube, PostHog, Search Console, orders, links).",
    },
    {
        "id": "ledger_src",
        "label": "Live ledger",
        "sub": "VPS · predictions vs reality",
        "lane": "source",
        "col": 0,
        "row": 2,
        "step": None,
        "desc": "Every new post is scored hourly on the VPS by both models before its results exist; real results are "
        "added after 24h, 72h and 7 days.",
    },
    {"id": "market", "label": "Market snapshot", "lane": "mac", "col": 1, "row": 0, "step": "market"},
    {"id": "pull", "label": "Warehouse pull", "lane": "mac", "col": 1, "row": 1, "step": "pull"},
    {"id": "ledger", "label": "Ledger download", "lane": "mac", "col": 1, "row": 2, "step": "ledger"},
    {"id": "judge", "label": "Judge (7 checks)", "lane": "mac", "col": 3, "row": 1, "step": "judge"},
    {"id": "package", "label": "Package", "lane": "mac", "col": 4, "row": 1, "step": "package"},
    {"id": "upload", "label": "VPS inbox", "sub": "upload-only", "lane": "vps", "col": 5, "row": 1, "step": "upload"},
    {
        "id": "knowledge",
        "label": "Knowledge pack",
        "sub": "gated",
        "lane": "mac",
        "col": 3,
        "row": 3,
        "step": "knowledge",
    },
    {"id": "report", "label": "Run report", "sub": "→ Telegram", "lane": "vps", "col": 5, "row": 2, "step": "report"},
    {
        "id": "approve",
        "label": "Your approval",
        "sub": "Telegram · web page",
        "lane": "vps",
        "col": 6,
        "row": 1,
        "step": None,
        "desc": "A model that passed every check waits for you: Approve / Reject in Telegram or on the Approvals page. "
        "Nothing goes live without you; rollback is one tap.",
    },
    {
        "id": "serve",
        "label": "Serving + measuring",
        "sub": "VPS · 24/7",
        "lane": "vps",
        "col": 6,
        "row": 2,
        "step": None,
        "desc": "intel-api scores drafts (/v1/predict, Try a draft) and every real post hourly, then records the real "
        "results: this is the live ledger the next training uses.",
    },
]
CORE_EDGES: list[tuple[str, str]] = [
    ("mip", "market"),
    ("warehouse", "pull"),
    ("ledger_src", "ledger"),
    ("judge", "package"),
    ("package", "upload"),
    ("judge", "report"),
    ("market", "knowledge"),
    ("pull", "knowledge"),
    ("knowledge", "report"),
    ("upload", "approve"),
    ("approve", "serve"),
    ("serve", "ledger_src"),
]
LANES = {"source": "Data sources", "mac": "Your Mac: weekly training", "vps": "VPS: approval and serving (24/7)"}


def steps(plugins: Sequence[ModelPlugin] | None = None) -> dict[str, tuple[str, str]]:
    """All steps of the Sunday run in order: core steps with the model plug-ins' steps before the judge."""
    ps = discover() if plugins is None else plugins
    keys = list(CORE_STEPS)
    at = keys.index("judge")
    ordered = keys[:at] + [p.step for p in ps] + keys[at:]
    own = {p.step: (p.label, p.desc) for p in ps}
    return {k: own.get(k) or CORE_STEPS[k] for k in ordered}


def nodes(plugins: Sequence[ModelPlugin] | None = None) -> list[dict[str, Any]]:
    ps = discover() if plugins is None else plugins
    boxes = [
        {"id": p.step, "label": p.box.label, "lane": "mac", "col": p.box.col, "row": p.box.row, "step": p.step}
        | ({"sub": p.box.sub} if p.box.sub else {})
        for p in ps
    ]
    return CORE_NODES + boxes


def edges(plugins: Sequence[ModelPlugin] | None = None) -> list[tuple[str, str]]:
    ps = discover() if plugins is None else plugins
    return CORE_EDGES + [(i, p.step) for p in ps for i in p.inputs] + [(p.step, "judge") for p in ps]


STEPS = steps()
NODES = nodes()
EDGES = edges()


def definition(plugins: Sequence[ModelPlugin] | None = None) -> dict[str, Any]:
    st, ns = steps(plugins), []
    for n in nodes(plugins):
        n = dict(n)
        if n.get("step") and "desc" not in n:
            n["desc"] = st[n["step"]][1]
        ns.append(n)
    return {
        "steps": [{"key": k, "label": v[0], "desc": v[1]} for k, v in st.items()],
        "nodes": ns,
        "edges": edges(plugins),
        "lanes": LANES,
    }
