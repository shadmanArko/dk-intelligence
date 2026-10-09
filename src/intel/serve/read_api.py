"""Read-only JSON routes for MCP and other machine clients (ADR 0025): they never compute, write or log anything.

- `GET /v1/monitoring/latest`: the last persisted monitoring result (kv 'monitoring') with its `at`, plus the
  guardrail mode and this process's recent guardrail decisions. Unlike `GET /v1/monitoring`, it never runs the
  monitor, so a read can't write.
- `GET /v1/insights/{name}`: one section of an active, approved insights model, through the field allowlist in
  `intel.exposure` (restricted fields never leave intel-api). Paged and capped at 64 KB.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query, Response

from intel.exposure import SECTIONS, section_data
from intel.serve.db import kv_get

MAX_BYTES = 64 * 1024
MAX_LIMIT = 200


class ModelLookup(Protocol):
    def get(self, name: str) -> dict[str, Any] | None: ...


def _filter_district(items: list[Any], district: str | None) -> list[Any]:
    if not district:
        return items
    want = district.casefold()
    return [i for i in items if isinstance(i, dict) and str(i.get("district", "")).casefold() == want]


def insights_payload(
    name: str,
    model: dict[str, Any] | None,
    section: str | None,
    district: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """The JSON body of GET /v1/insights/{name}; raises HTTPException for unknown or inactive models."""
    sections = SECTIONS.get(name)
    if sections is None:
        raise HTTPException(404, f"unknown insights model; known: {', '.join(sorted(SECTIONS))}")
    if model is None or model["bundle"].get("kind") != "insights":
        raise HTTPException(404, "not_active")
    head = {"name": name, "version": model["version"], "package_id": model["package_id"]}
    if section is None:
        return {**head, "sections": {k: {"kind": s.kind, "note": s.note} for k, s in sections.items()}}
    if section not in sections:
        raise HTTPException(422, f"unknown section; one of: {', '.join(sections)}")
    spec = sections[section]
    data = section_data(name, section, model["bundle"]["insights"])
    out: dict[str, Any] = {**head, "section": section, "note": spec.note}
    if spec.kind == "object":
        return {**out, "data": data}
    items = _filter_district(data, district)
    page = items[offset : offset + limit]
    body = {**out, "items": page, "total": len(items), "offset": offset, "truncated": False}
    while page and len(json.dumps(body, default=str)) > MAX_BYTES:  # a hard cap, whatever the bundle holds
        page = page[: max(1, len(page) * 3 // 4)] if len(page) > 1 else []
        body = {**body, "items": page, "truncated": True}
    return body


def register_read_routes(
    app: FastAPI,
    models: ModelLookup,
    dsn: str,
    guardrail_mode: str,
    guardrail_decisions: Callable[[], list[dict[str, Any]] | None],
) -> None:
    @app.get("/v1/monitoring/latest")
    def monitoring_latest() -> dict[str, Any]:
        guard = {"mode": guardrail_mode, "decisions": guardrail_decisions() or []}
        row = kv_get(dsn, "monitoring")
        if row is None:
            return {"status": "not_computed_yet", "guardrail": guard}
        return {**row, "status": "ok", "guardrail": guard}

    @app.get("/v1/insights/{name}")
    def insights(
        name: str,
        response: Response,
        section: str | None = None,
        district: str | None = None,
        limit: int = Query(50, ge=1, le=MAX_LIMIT),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        body = insights_payload(name, models.get(name), section, district, limit, offset)
        response.headers["ETag"] = f'"{body["package_id"]}"'
        return body
