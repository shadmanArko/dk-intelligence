"""The ONE description of the intelligence pipeline, used by:
- the Sunday run (`intel.weekly`: which steps exist and their order),
- the Control Room on the Mac and the Training page on the VPS (the diagram),
- Telegram /training.

To add something later (a new model, data source or step): add a STEP (if the Sunday run executes it) and a NODE
(where it sits in the diagram, what it does) plus its EDGES. Nothing else needs to change for it to be shown.
"""

from __future__ import annotations

# Steps the Sunday run executes, in order. key -> (label, what it does)
STEPS: dict[str, tuple[str, str]] = {
    "wait": (
        "Wait for the data refresh",
        "The market data platform refreshes every Sunday at 01:00 on this Mac (Wolt, Lieferando, Reddit, YouTube, "
        "events, ...). Training waits until that has finished, so it always uses this week's market data.",
    ),
    "pull": (
        "Pull the warehouse",
        "Copies your business data from the VPS warehouse: posts, their metrics over time, website visits, Search "
        "Console, orders. Read-only database role, through an SSH tunnel over Tailscale that can only reach the "
        "database. Saved as a verified, immutable snapshot (Parquet + checksums).",
    ),
    "market": (
        "Market snapshot",
        "Reads what the data platform learned from ~1.9M public posts: which topics, languages and formats "
        "perform, the occasion calendar per community, and the topic dictionary.",
    ),
    "ledger": (
        "Download the live ledger",
        "Fetches from the VPS what each model predicted for every real post BEFORE its results existed, plus "
        "the real results after 24h / 72h / 7 days. Used to check new models against reality.",
    ),
    "train": (
        "Train candidate models",
        "Two questions, one model each: content_scorer (more likes + comments + shares than usual?) and reach_scorer "
        "(more people reached than our last 20 posts?). Features use only what is known before posting; several "
        "candidates per question are trained on all posts except the newest 20% and chosen by time-ordered "
        "cross-validation.",
    ),
    "mine": (
        "Mine competitor reviews",
        "Reads ~13.7k Lieferando reviews of the Berlin places that sell biryani, splits them into clauses and finds "
        "14 topics (delivery time, rice, meat, portion, wrong items, ...) with praise or complaint, per competitor "
        "and district. Every decision is a readable dictionary phrase; your 200-sentence check measures accuracy.",
    ),
    "community": (
        "Community map",
        "Scores all 542 Berlin planning areas: residents of South Asian origin, meat-and-rice food cultures, "
        "international food explorers and young small households (public statistics), times a reach factor for the "
        "delivery bands from Leopoldplatz. Flags gaps: big audience, no biryani seller. Chooses AREAS, never people.",
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
    "report": (
        "Report the result",
        "Uploads a short run report; the VPS turns it into a Telegram message (new model waiting, no "
        "improvement, or failed at step X).",
    ),
}

# Diagram. lane: source | mac | vps. col/row: grid position. step: which run step drives its colour (None = a
# service that is always there).
NODES: list[dict] = [
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
    {"id": "train", "label": "Train candidates", "lane": "mac", "col": 2, "row": 1, "step": "train"},
    {
        "id": "mine",
        "label": "Review miner",
        "sub": "competitor weaknesses",
        "lane": "mac",
        "col": 2,
        "row": 0,
        "step": "mine",
    },
    {
        "id": "community",
        "label": "Community map",
        "sub": "542 Berlin areas",
        "lane": "mac",
        "col": 3,
        "row": 0,
        "step": "community",
    },
    {"id": "judge", "label": "Judge (7 checks)", "lane": "mac", "col": 3, "row": 1, "step": "judge"},
    {"id": "package", "label": "Package", "lane": "mac", "col": 4, "row": 1, "step": "package"},
    {"id": "upload", "label": "VPS inbox", "sub": "upload-only", "lane": "vps", "col": 5, "row": 1, "step": "upload"},
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
EDGES: list[tuple[str, str]] = [
    ("mip", "market"),
    ("warehouse", "pull"),
    ("ledger_src", "ledger"),
    ("market", "train"),
    ("pull", "train"),
    ("ledger", "train"),
    ("train", "judge"),
    ("market", "mine"),
    ("mine", "judge"),
    ("market", "community"),
    ("community", "judge"),
    ("judge", "package"),
    ("package", "upload"),
    ("judge", "report"),
    ("upload", "approve"),
    ("approve", "serve"),
    ("serve", "ledger_src"),
]
LANES = {"source": "Data sources", "mac": "Your Mac: weekly training", "vps": "VPS: approval and serving (24/7)"}


def definition() -> dict:
    nodes = []
    for n in NODES:
        n = dict(n)
        if n.get("step") and "desc" not in n:
            n["desc"] = STEPS[n["step"]][1]
        nodes.append(n)
    return {
        "steps": [{"key": k, "label": v[0], "desc": v[1]} for k, v in STEPS.items()],
        "nodes": nodes,
        "edges": EDGES,
        "lanes": LANES,
    }
