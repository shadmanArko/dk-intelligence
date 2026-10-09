"""model_status: which models run, their health, the ledger, training and pending approvals (owner only)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from intel.capabilities.ports import IntelApi
from intel.capabilities.types import ResponseFormat, ToolProblem, prov

ACTOR_ROLE = {"telegram": "owner", "web": "owner", "ci": "ci", "system": "system", "guardrail": "system"}


def _optional(fetch: Callable[[], Any]) -> tuple[Any, str | None]:
    """A part of the status that may be missing (an older intel-api, a route not deployed yet)."""
    try:
        return fetch(), None
    except ToolProblem as e:
        return None, e.code


def model_status(api: IntelApi, kinds: dict[str, str], response_format: ResponseFormat = "concise") -> dict[str, Any]:
    ready = api.get("/ready")
    models = [
        {"name": n, "kind": kinds.get(n, "unknown"), "version": v, "health": "active"}
        for n, v in sorted((ready.get("models") or {}).items())
    ]
    monitoring_raw, problem = _optional(lambda: api.get("/v1/monitoring/latest"))
    if monitoring_raw is None:
        monitoring: dict[str, Any] = {
            "status": "route_not_deployed" if problem == "UPSTREAM_UNAVAILABLE" else "unavailable"
        }
        guardrail_mode = None
    else:
        guardrail_mode = (monitoring_raw.get("guardrail") or {}).get("mode")
        reasons = [str(r) for r in monitoring_raw.get("reasons") or []]
        monitoring = {
            "status": monitoring_raw.get("status", "ok"),
            "at": monitoring_raw.get("at"),
            "retrain_requested": monitoring_raw.get("retrain_requested"),
            "reasons": reasons[:10],
        }
    ledger, _ = _optional(lambda: api.get("/v1/ledger/summary", {"horizon": "7d"}))
    training, _ = _optional(lambda: api.get("/api/training-state"))
    audit, _ = _optional(lambda: api.get("/v1/audit", {"limit": 10 if response_format == "detailed" else 1}))
    last = (training or {}).get("history") or []
    out: dict[str, Any] = {
        "api_version": ready.get("version"),
        "models": models,
        "monitoring": monitoring,
        "ledger_7d": None
        if ledger is None
        else {
            "posts": ledger.get("posts"),
            "outcomes": ledger.get("outcomes"),
            "models": {
                n: {"n": m.get("n"), "accuracy": m.get("accuracy"), "balanced_accuracy": m.get("balanced_accuracy")}
                for n, m in (ledger.get("models") or {}).items()
            },
        },
        "last_training": {"run_id": last[0].get("run_id"), "status": last[0].get("status")} if last else None,
        "pending_approval": [
            {"name": p["name"], "version": p["version"], "received_at": p.get("received_at")}
            for p in (audit or {}).get("packages", [])
            if p.get("stage") == "candidate"
        ],
        "guardrail_mode": guardrail_mode,
    }
    if response_format == "detailed" and audit:
        out["audit_chain_ok"] = (audit.get("chain") or {}).get("ok")
        out["recent_decisions"] = [
            {
                "at": e.get("at"),
                "action": e.get("action"),
                "subject": e.get("subject"),
                "actor_role": ACTOR_ROLE.get(str(e.get("channel")), "owner"),
            }
            for e in (audit.get("entries") or [])[:10]
        ]
    return {**out, "prov": prov("intel-api", ready.get("version"))}
