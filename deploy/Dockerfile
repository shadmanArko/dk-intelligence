# intel-api image (ADR 0014). Two stages: a builder with uv resolves the locked runtime dependencies; the runtime stage
# gets only the virtualenv and the code. No compiler, no uv, no pip and no download cache in the final image.
# Every image is pinned by digest (both stages use the same base); Dependabot proposes updates.
FROM python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-install-project --no-editable
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-editable

FROM python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c AS runtime
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
 && mkdir -p /store && chown intel:intel /store
COPY --from=builder /app/.venv /app/.venv
WORKDIR /app
USER intel
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    INTEL_STORE_DIR=/store INTEL_INBOX_DIR=/inbox INTEL_GIT_SHA=${GIT_SHA}
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["python", "-m", "intel.serve.app"]
