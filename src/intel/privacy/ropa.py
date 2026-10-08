"""The record of processing (GDPR Art. 30 style), rendered from policy.yaml. The committed copy
(docs/privacy/RECORD_OF_PROCESSING.md) must equal the rendering; a test fails when the policy changes alone."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from intel.privacy import policy

ROPA_FILE = Path(__file__).resolve().parents[3] / "docs" / "privacy" / "RECORD_OF_PROCESSING.md"

LEVELS = {
    "none": "no personal data",
    "pseudonymous": "pseudonymous (personal data)",
    "declared": "**declared personal data**",
}


def _cell(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


def _retention(spec: dict[str, Any]) -> str:
    r = spec.get("retention")
    if isinstance(r, int):
        out = f"{r} days (by `{spec['column']}`)"
    elif r == "keep":
        out = f"kept: {spec.get('why', '')}"
    else:
        out = "newest 12 snapshots"
    if part := spec.get("delete_after_days"):
        out += f"; `{part['where']}` rows: {part['days']} days"
    return out


def _table(section: str) -> list[str]:
    sec = policy()[section]
    rows = [
        f"Stored: {sec['where']}.",
        "",
        "| Table | Purpose | Personal data | Categories / notes | Retention |",
        "|---|---|---|---|---|",
    ]
    for name, spec in sec["tables"].items():
        notes = "; ".join(_cell(spec[k]) for k in ("categories", "note") if spec.get(k))
        extra = []
        if spec.get("protected_derived"):
            extra.append("derived from residents by origin / citizenship (see fairness)")
        if spec.get("redact"):
            extra.append(f"emails and phone numbers redacted in {', '.join(spec['redact'])}")
        notes = "; ".join(n for n in [notes, *extra] if n)
        level = LEVELS[spec["personal"]]
        rows.append(f"| `{name}` | {_cell(spec['purpose'])} | {level} | {notes or '-'} | {_retention(spec)} |")
    return rows


def render() -> str:
    p = policy()
    out = [
        "# Record of processing",
        "",
        "<!-- Generated from src/intel/privacy/policy.yaml by `intel privacy ropa --write`. Do not edit by hand. -->",
        "",
        f"Policy version {p['version']}, updated {p['updated']}. Controller: {p['controller']}.",
        "",
        "## Purposes",
        "- Decide what to post, when and where (content and reach scorers, occasion planner, creator matcher).",
        "- Understand the market and the business (competitor weakness miner, community map, funnel).",
        "- Run the service safely (monitoring, audit trail, backups).",
        "",
        "No automated decision with legal or similarly significant effect on a person is made: every model advises the",
        "owner, who decides. No data is sold or shared; processors are the VPS host and Backblaze B2 (EU) for backups.",
        "",
        "## Warehouse snapshots (only these tables leave the VPS)",
        *_table("warehouse_pull"),
        "",
        "## Market snapshots (data platform, fixed queries)",
        *_table("market_pull"),
        "",
        "## Serving database (intel-db)",
        *_table("intel_db"),
        "",
        "## Files and copies",
        "| What | Where | Retention | Enforced by |",
        "|---|---|---|---|",
        *[
            f"| {_cell(f['what'])} | {_cell(f['where'])} | "
            f"{_cell(f['retention'])}{' days' if isinstance(f['retention'], int) else ''} | {_cell(f['enforced_by'])} |"
            for f in p["files"]
        ],
        "",
        "## Protected attributes",
        f"Never used in a field name or produced: {', '.join(p['fairness']['protected_terms'])}, and the data",
        "platform's communities of origin. Reviewed exceptions (aggregate statistics, never individuals):",
        "",
        "| Model | Reads | Basis | Use | Reviewed | Review again |",
        "|---|---|---|---|---|---|",
        *[
            f"| {e['model']} | {', '.join(e['reads'])} | {_cell(e['basis'])} | {_cell(e['use'])} | "
            f"{e['reviewed_by']}, {e['reviewed_on']} | {e['review_again']} |"
            for e in p["fairness"]["exceptions"]
        ],
        "",
        "## Safeguards",
        "- A pull copies declared tables only; undeclared personal data fails the pull (counts, never values).",
        "- Every package passes the responsible-AI gate (protected attributes by name and by lineage, blocked words in",
        "  generated text, personal data) before it is built.",
        "- Deletion runs daily in intel-api, in one transaction with its audit entry; a stalled job raises an alert.",
        "- Identities: see [HASHING.md](HASHING.md) for what is hashed, with which salt, and what is never copied.",
        "",
    ]
    return "\n".join(out)
