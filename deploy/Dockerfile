# intel-api image. Installs the locked runtime dependencies only (no dev, no MLflow).
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-default-groups --no-install-project
COPY src ./src
RUN uv sync --locked --no-default-groups \
 && useradd --uid 10001 --create-home intel \
 && mkdir -p /store && chown intel:intel /store
USER intel
ENV PATH="/app/.venv/bin:$PATH" INTEL_STORE_DIR=/store INTEL_INBOX_DIR=/inbox
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
CMD ["python", "-m", "intel.serve.app"]
