"""Prometheus metrics for dk_intel (architecture §7.4), in their own registry: the MCP process never mixes its
series with intel-api's. Served at /metrics on the HTTP app (loopback only); on the VPS (P4.7) Prometheus scrapes it.
Names follow the design: intel_mcp_tool_calls_total{tool,profile,status}, intel_mcp_tool_latency_seconds{tool},
intel_mcp_result_tokens_est{tool}, intel_mcp_auth_failures_total{reason}, intel_mcp_cache_hits_total{tool}.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, ProcessCollector, generate_latest

REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)  # process_resident_memory_bytes: the memory alert near mem_limit
CALLS = Counter("intel_mcp_tool_calls", "Tool calls", ["tool", "profile", "status"], registry=REGISTRY)
LATENCY = Histogram(
    "intel_mcp_tool_latency_seconds",
    "Tool latency",
    ["tool"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
    registry=REGISTRY,
)
TOKENS = Histogram(
    "intel_mcp_result_tokens_est",
    "Result size in tokens (bytes / 3)",
    ["tool"],
    buckets=(100, 250, 500, 1000, 2000, 4000, 8000),
    registry=REGISTRY,
)
AUTH_FAILURES = Counter("intel_mcp_auth_failures", "Refused HTTP requests", ["reason"], registry=REGISTRY)
CACHE_HITS = Counter("intel_mcp_cache_hits", "Results served from the cache", ["tool"], registry=REGISTRY)


def observe(tool: str, profile: str, status: str, seconds: float, tokens: int | None) -> None:
    CALLS.labels(tool, profile, status).inc()
    LATENCY.labels(tool).observe(seconds)
    if tokens is not None:
        TOKENS.labels(tool).observe(tokens)


def exposition() -> bytes:
    return generate_latest(REGISTRY)
