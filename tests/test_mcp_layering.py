"""The MCP layering rules (architecture §3.1) and the intel-api allowlist (§2.3, §2.7), checked statically."""

import ast
from pathlib import Path

import httpx
import pytest

from intel.adapters.intel_api import ALLOWED, HttpIntelApi, ScoringLimits, route_template
from intel.capabilities.types import ToolProblem

SRC = Path(__file__).resolve().parents[1] / "src" / "intel"

# folder -> import prefixes it must never use
FORBIDDEN = {
    "capabilities": ("fastmcp", "mcp", "httpx", "psycopg", "intel.adapters", "intel.serve", "intel.mcp_server"),
    "adapters": ("fastmcp", "mcp", "intel.mcp_server", "intel.serve"),
    "mcp_server": ("intel.tunnel", "intel.pull", "intel.serve"),
    "serve": ("fastmcp", "mcp", "intel.mcp_server", "intel.capabilities", "intel.adapters", "onnxruntime"),
}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("folder", sorted(FORBIDDEN))
def test_layers_import_only_what_they_may(folder):
    bad = []
    for f in sorted((SRC / folder).rglob("*.py")):
        for name in _imports(f):
            if any(name == p or name.startswith(p + ".") for p in FORBIDDEN[folder]):
                bad.append(f"{f.relative_to(SRC)} imports {name}")
    assert bad == []


def test_exposure_is_import_light():
    assert _imports(SRC / "exposure.py") <= {"__future__", "collections.abc", "dataclasses", "typing"}


def test_no_fastapi_auto_conversion_and_no_warehouse_credentials():
    text = "\n".join(f.read_text() for f in (SRC / "mcp_server").rglob("*.py"))
    assert "from_fastapi" not in text and "from_openapi" not in text
    assert "dk_warehouse" not in text.lower() and "mip_reader" not in text.lower()


def _app_routes() -> set[tuple[str, str]]:
    from intel.serve.app import create_app
    from intel.settings import Settings

    app = create_app(Settings(_env_file=None, intel_db_dsn="postgresql://x@127.0.0.1:1/x"), start_workers=False)
    return {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or set())}


def test_every_allowed_route_exists_and_the_only_post_is_the_batch_scorer():
    routes = _app_routes()
    assert routes >= ALLOWED
    assert {p for m, p in ALLOWED if m == "POST"} == {"/v1/score/batch"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/predict"),
        ("POST", "/v1/score"),
        ("POST", "/try"),
        ("POST", "/approvals/1/approve"),
        ("POST", "/models/content_scorer/rollback"),
        ("GET", "/v1/ledger/export"),
        ("GET", "/v1/monitoring"),
        ("GET", "/metrics"),
        ("GET", "/"),
        ("GET", "/v1/insights/../ledger/export"),
        ("DELETE", "/ready"),
    ],
)
def test_everything_else_is_refused_before_any_io(method, path, tmp_path):
    with pytest.raises(PermissionError):
        route_template(method, path)
    calls = []
    api = HttpIntelApi(
        "http://intel.test", ScoringLimits(tmp_path), transport=httpx.MockTransport(lambda r: calls.append(r))
    )
    with pytest.raises(PermissionError):
        api._request(method, path)
    assert calls == []


def test_requests_carry_a_request_id_and_never_credentials(tmp_path):
    from intel.adapters.intel_api import REQUEST_ID

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ready", "models": {}})

    api = HttpIntelApi("http://intel.test", ScoringLimits(tmp_path), transport=httpx.MockTransport(handler))
    token = REQUEST_ID.set("abc123")
    try:
        api.get("/ready")
    finally:
        REQUEST_ID.reset(token)
    (req,) = seen
    assert req.headers["x-request-id"] == "abc123"
    assert "authorization" not in req.headers and "cookie" not in req.headers


def test_upstream_failures_retry_once_then_say_unavailable(tmp_path):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(502)

    api = HttpIntelApi(
        "http://intel.test", ScoringLimits(tmp_path), transport=httpx.MockTransport(handler), retry_after_s=0
    )
    with pytest.raises(ToolProblem) as e:
        api.get("/ready")
    assert e.value.code == "UPSTREAM_UNAVAILABLE" and len(attempts) == 2


def test_not_active_and_missing_routes_map_to_clear_codes(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if "weakness" in request.url.path:
            return httpx.Response(404, json={"detail": "not_active"})
        return httpx.Response(404, json={"detail": "Not Found"})

    api = HttpIntelApi("http://intel.test", ScoringLimits(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(ToolProblem) as e:
        api.get("/v1/insights/weakness_miner")
    assert e.value.code == "MODEL_NOT_ACTIVE"
    with pytest.raises(ToolProblem) as e:
        api.get("/v1/monitoring/latest")
    assert e.value.code == "UPSTREAM_UNAVAILABLE" and "not deployed" in e.value.message
