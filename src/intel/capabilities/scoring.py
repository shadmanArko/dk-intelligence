"""score_drafts: the active production scorers' verdict on drafts, with plain reasons (never writes the ledger)."""

from __future__ import annotations

from typing import Any

from intel.capabilities.ports import IntelApi
from intel.capabilities.types import SCORABLE, Draft, ResponseFormat, ToolProblem, prov, refs
from intel.explain import reason_text

MAX_DRAFTS = 50
MEANING = (
    "probability that the post beats our typical engagement for its platform; above the model's threshold means "
    "'likely above typical'. A forecast from past posts, not a guarantee."
)


def score_drafts(
    api: IntelApi, drafts: list[Draft], principal: str, response_format: ResponseFormat = "concise"
) -> dict[str, Any]:
    if not 1 <= len(drafts) <= MAX_DRAFTS:
        raise ToolProblem("INVALID_ARGUMENT", f"send 1 to {MAX_DRAFTS} drafts, not {len(drafts)}")
    names = refs(drafts)
    scorable: list[tuple[str, Draft]] = []
    not_scored: list[dict[str, str]] = []
    for ref, d in zip(names, drafts, strict=True):
        if d.platform not in SCORABLE:
            not_scored.append({"ref": ref, "reason": f"no scorer for {d.platform}; use check_drafts"})
        elif d.planned_posted_at is None:
            not_scored.append({"ref": ref, "reason": "needs planned_posted_at with a timezone"})
        else:
            scorable.append((ref, d))
    result: dict[str, Any] = {"model_versions": {}, "ranked": [], "not_scored": not_scored, "meaning": MEANING}
    if not scorable:
        return {**result, "prov": prov("intel-api /v1/score/batch")}
    payload = [
        {
            "platform": d.platform,
            "content_type": d.content_type or d.format,
            "caption": d.caption,
            "planned_posted_at": d.planned_posted_at.isoformat() if d.planned_posted_at else None,
        }
        for _, d in scorable
    ]
    answer = api.score_batch(payload, principal)
    models: dict[str, list[dict[str, Any]]] = answer.get("models") or {}
    if not models:
        raise ToolProblem("MODEL_NOT_ACTIVE", "no scorer is active on intel-api")
    primary = "content_scorer" if "content_scorer" in models else sorted(models)[0]
    ranked: list[dict[str, Any]] = []
    for i, (ref, _) in enumerate(scorable):
        scores = {}
        for name, preds in models.items():
            p = preds[i]
            scores[name] = {
                "probability": p["probability"],
                "threshold": p["threshold"],
                "label": "likely_above_typical" if p["label"] else "likely_below_typical",
            }
        top = models[primary][i].get("top_reasons") or []
        if response_format == "detailed":
            reasons: list[Any] = [
                {"reason": reason_text(r["feature"]), "feature": r["feature"], "contribution": r["contribution"]}
                for r in top[:3]
            ]
        else:
            reasons = [("+ " if r["contribution"] > 0 else "- ") + reason_text(r["feature"]) for r in top[:3]]
        ranked.append({"ref": ref, "scores": scores, "reasons": reasons})
    ranked.sort(key=lambda r: -r["scores"][primary]["probability"])
    for rank, r in enumerate(ranked, start=1):
        r["rank"] = rank
    result["model_versions"] = {name: preds[0]["model_version"] for name, preds in models.items() if preds}
    result["ranked"] = ranked
    return {**result, "prov": prov("intel-api /v1/score/batch", result["model_versions"].get(primary))}
