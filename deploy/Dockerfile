# intel-api image (ADR 0014) and, from the same commit, the intel-mcp image (target `mcp`, ADR 0030). Two stages each: a builder with uv resolves the locked runtime dependencies; the runtime stage
# gets only the virtualenv and the code. No compiler, no uv, no pip and no download cache in the final image.
# Every image is pinned by digest (both stages use the same base); Dependabot proposes updates.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-install-project --no-editable
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-editable

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="dk-intelligence intel-api" \
      org.opencontainers.image.source="https://github.com/shadmanArko/dk-intelligence" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}"
# pip (with its vendored urllib3 / msgpack) is not used at runtime: removing it removes its vulnerabilities too.
RUN python -m pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3.13/ensurepip /root/.cache \
 && useradd --uid 10001 --create-home intel \
 && mkdir -p /store /knowledge && chown intel:intel /store /knowledge
COPY --from=builder /app/.venv /app/.venv
WORKDIR /app
USER intel
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    INTEL_STORE_DIR=/store INTEL_INBOX_DIR=/inbox INTEL_GIT_SHA=${GIT_SHA}
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["python", "-m", "intel.serve.app"]


# ---------- intel-mcp (ADR 0030): the MCP server for AI clients, its own image ----------
# Built from the same commit with only the `mcp` and `rag` groups (FastMCP, onnxruntime, tokenizers; no torch), so
# intel-api's image above keeps its dependencies and its scan surface. Models and knowledge packs are NOT in the image:
# they arrive through the upload-only inbox into the read-only knowledge volume.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS mcp-builder
COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --group mcp --group rag --no-install-project --no-editable
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --group mcp --group rag --no-editable

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS mcp
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="dk-intelligence intel-mcp" \
      org.opencontainers.image.source="https://github.com/shadmanArko/dk-intelligence" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}"
RUN python -m pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3.13/ensurepip /root/.cache \
 && useradd --uid 10001 --create-home intel \
 && mkdir -p /data /knowledge && chown intel:intel /data /knowledge
COPY --from=mcp-builder /app/.venv /app/.venv
WORKDIR /app
USER intel
# No outbound downloads at runtime (models come from the knowledge volume); listeners on all interfaces INSIDE the
# container, of which compose publishes only the tailnet one, on the host's loopback.
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 \
    FASTMCP_TELEMETRY_MODE=propagation_only INTEL_DATA_DIR=/data INTEL_PACK_DIR=/knowledge/packs \
    INTEL_MODEL_DIR=/knowledge/models INTEL_MCP_BIND=0.0.0.0 INTEL_MCP_HOST=vps INTEL_MCP_PACK_SYNC=true \
    INTEL_GIT_SHA=${GIT_SHA}
EXPOSE 8766 8767
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8766/healthz').status==200 else 1)"
CMD ["python", "-m", "intel.mcp_server.cli", "serve", "--transport", "http", "--listener", "both"]
