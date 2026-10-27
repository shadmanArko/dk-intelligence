"""brand_rules and get_facts: the brand truth from the active knowledge pack (deterministic, offline)."""

from __future__ import annotations

from typing import Any, Literal

import yaml

from intel.capabilities.ports import BrandPack, BrandSource
from intel.capabilities.types import ToolProblem, prov

# Hard caption limits of each platform's API (characters); the playbook has no caption ranges yet.
CAPTION_LIMIT: dict[str, int] = {
    "instagram": 2200,
    "facebook": 63206,
    "tiktok": 4000,
    "youtube": 5000,
    "threads": 500,
    "x": 280,
    "linkedin": 3000,
    "reddit": 40000,
}
FIRST_LINE_MAX = 60
EMOJI_MAX = 4
# Where a platform reads differently from the default: LinkedIn's hook is a full formal sentence, and a company
# voice there uses few emojis.
FIRST_LINE_MAX_BY = {"linkedin": 140}
EMOJI_MAX_BY = {"linkedin": 2}


def first_line_max(platform: str) -> int:
    return FIRST_LINE_MAX_BY.get(platform, FIRST_LINE_MAX)


def emoji_max(platform: str) -> int:
    return EMOJI_MAX_BY.get(platform, EMOJI_MAX)


FACT_GROUPS = ("menu", "ordering", "fulfilment", "claims_status")
ALLERGEN_REPLY = "We can't answer allergen questions here; please ask us directly."


def platform_formats(pack: BrandPack, platform: str) -> list[str]:
    plan = (pack.playbook.get("platforms") or {}).get(platform) or {}
    return list(dict.fromkeys(str(s["format"]) for s in plan.get("daily") or []))


def brand_rules(
    source: BrandSource,
    platform: str | None = None,
    format: str | None = None,
    section: Literal["voice", "platform", "legal", "all"] = "all",
) -> dict[str, Any]:
    pack = source.load()
    pb = pack.playbook
    brand = pb.get("brand") or {}
    formats = pb.get("formats") or {}
    if format is not None and format not in formats:
        raise ToolProblem("INVALID_ARGUMENT", f"unknown format {format!r}", f"one of: {', '.join(sorted(formats))}")
    if platform is not None and platform not in CAPTION_LIMIT:
        raise ToolProblem("INVALID_ARGUMENT", f"unknown platform {platform!r}", ", ".join(sorted(CAPTION_LIMIT)))
    out: dict[str, Any] = {"brand_version": pack.pack_id}
    if section in ("voice", "all"):
        out["voice"] = brand.get("voice")
        out["always_say"] = brand.get("always_say") or []
        out["signature"] = brand.get("signature")
        out["languages"] = {
            "order": brand.get("base_languages") or ["de", "en"],
            "rules": "German first, then one English line; Bengali or Banglish for community posts",
        }
    if section in ("legal", "all"):
        out["never"] = brand.get("never") or []
        out["paid_label"] = brand.get("paid_partnership_label")
        out["impressum"] = brand.get("impressum")
        out["blocked_words"] = "not listed here: run check_drafts on every draft"
    if section in ("platform", "all") and platform is not None:
        plan = ((pb.get("platforms") or {}).get(platform) or {}).get("daily") or []
        chosen = [format] if format else platform_formats(pack, platform)
        out["platform"] = {
            "name": platform,
            "cadence": plan,
            "windows": pb.get("windows") or {},
            "caption_max_chars": CAPTION_LIMIT[platform],
            "first_line_max": first_line_max(platform),
            "emoji_max": emoji_max(platform),
            "formats": {f: formats.get(f) for f in chosen if f in formats},
            "audio": (pb.get("audio") or {}).get(platform),
            "occasions_allowed": platform not in (pb.get("no_occasion_platforms") or []),
        }
        out["hashtags"] = {k: v for k, v in (pb.get("hashtags") or {}).items() if k != "occasion"}
    open_questions = sum(doc.count("[CONFIRM") for doc in pack.documents.values())
    out["open_questions_count"] = open_questions
    out["sources"] = pack.sources
    return {**out, "prov": prov("knowledge pack (brand)", pack.pack_id)}


def _confirm_lines(text: str) -> set[int]:
    return {i for i, line in enumerate(text.splitlines()) if "[CONFIRM" in line}


def _leaves(node: yaml.Node, path: str, out: list[tuple[str, yaml.Node]]) -> None:
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            _leaves(v, f"{path}.{k.value}" if path else str(k.value), out)
    elif isinstance(node, yaml.SequenceNode):
        for i, v in enumerate(node.value):
            key = None
            if isinstance(v, yaml.MappingNode):
                key = next((vv.value for kk, vv in v.value if kk.value == "id"), None)
            _leaves(v, f"{path}.{key if key is not None else i}", out)
    else:
        out.append((path, node))


def get_facts(source: BrandSource, keys: list[str] | None = None) -> dict[str, Any]:
    """Every fact with a status: confirmed, unconfirmed ([CONFIRM] in the file) or unknown (null). Never a guess."""
    pack = source.load()
    wanted = keys or list(FACT_GROUPS)
    unknown = [k for k in wanted if k not in FACT_GROUPS]
    if unknown:
        raise ToolProblem("INVALID_ARGUMENT", f"unknown fact groups {unknown}", f"any of: {', '.join(FACT_GROUPS)}")
    text = pack.documents.get("facts.yaml", "")
    root = yaml.compose(text)
    confirm = _confirm_lines(text)
    leaves: list[tuple[str, yaml.Node]] = []
    if root is not None:
        _leaves(root, "", leaves)
    values = {path: yaml.safe_load(yaml.serialize(node)) for path, node in leaves}
    items = []
    for path, node in leaves:
        if path.split(".", 1)[0] not in wanted or path.endswith(".id"):
            continue
        value = values[path]
        if value is None:
            status, say = "unknown", "not confirmed: don't state it"
        elif node.start_mark.line in confirm:
            status, say = "unconfirmed", "the owner hasn't confirmed this: don't state it publicly yet"
        else:
            status, say = "confirmed", "may be stated"
        items.append({"key": path, "value": value, "status": status, "say": say})
    return {
        "facts_version": pack.facts.get("version"),
        "status": "DRAFT" if "draft" in str(pack.facts.get("version", "")).lower() else "FINAL",
        "items": items,
        "never_answer": ["allergens"],
        "allergen_reply": ALLERGEN_REPLY,
        "prov": prov("knowledge pack (facts.yaml)", pack.pack_id),
    }
