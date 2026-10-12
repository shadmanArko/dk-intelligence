"""MCP load test (architecture §7.6): N concurrent clients over HTTP against a running server, a realistic mix of
tools, p50/p95 and errors per tool. Mac only, by hand: `uv run intel mcp loadtest --clients 10 --rounds 5`
(start `uv run intel mcp serve --transport http` first). The token is minted in memory and never shown.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

MIX: list[tuple[str, dict[str, Any]]] = [
    ("get_facts", {}),
    ("brand_rules", {"platform": "instagram"}),
    ("upcoming_occasions", {"days": 14, "platform": "instagram"}),
    ("competitor_complaints", {"limit": 5}),
    ("trend_signals", {"platform": "tiktok"}),
    ("search_reviews", {"query": "Lieferung kam kalt an", "limit": 5}),
    ("search_posts", {"query": "Borhani", "source": "own", "limit": 5}),
    ("evaluate_drafts", {"drafts": [{"platform": "instagram", "format": "reel",
                                     "planned_posted_at": "2026-10-16T17:00:00+02:00",
                                     "caption": "Samstag gibt es frische Kacchi in Berlin.\nSlow-cooked mutton.\n"
                                                "#kacchibiryani #berlinfood #biryaniberlin"}]}),
]  # fmt: skip


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1) if s else 0.0


async def run(url: str, tokens: list[str], clients: int, rounds: int) -> dict[str, Any]:
    """One token per client: each client is its own principal, as separate agents would be."""
    from fastmcp import Client

    times: dict[str, list[float]] = {name: [] for name, _ in MIX}
    errors: dict[str, list[str]] = {name: [] for name, _ in MIX}

    async def one(i: int) -> None:
        async with Client(url, auth=tokens[i % len(tokens)]) as c:
            for r in range(rounds):
                for k in range(len(MIX)):
                    name, args = MIX[(k + i + r) % len(MIX)]  # clients start at different tools
                    t0 = time.perf_counter()
                    res = await c.call_tool(name, args, raise_on_error=False)
                    ms = (time.perf_counter() - t0) * 1000
                    if res.is_error:
                        errors[name].append(" ".join(getattr(b, "text", "") for b in res.content)[:80])
                    else:
                        times[name].append(ms)

    t0 = time.perf_counter()
    await asyncio.gather(*(one(i) for i in range(clients)))
    wall = time.perf_counter() - t0
    calls = sum(len(v) for v in times.values()) + sum(len(v) for v in errors.values())
    return {
        "clients": clients,
        "rounds": rounds,
        "calls": calls,
        "seconds": round(wall, 1),
        "calls_per_s": round(calls / wall, 2),
        "tools": {
            name: {
                "ok": len(times[name]),
                "errors": len(errors[name]),
                "p50_ms": _pct(times[name], 0.5),
                "p95_ms": _pct(times[name], 0.95),
                "error_sample": errors[name][:1],
            }
            for name, _ in MIX
        },
    }
