# The Sunday training on the homelab server (ADR 0044): pull -> market snapshot -> ledger -> train + judge -> package
# -> upload through door 2. Build context is the repo root:  docker compose -f deploy/server/compose.yaml build
# amd64 (the server). CPU only: scikit-learn models and the int8 ONNX embedder; no torch, no GPU.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# train (mlflow, pandera) + rag (ONNX embedder for the knowledge pack) + mcp (default group the CLI imports)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --group train --group rag --group mcp --no-install-project --no-editable
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --group train --group rag --group mcp --no-editable

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="dk-intelligence weekly (homelab server)" \
      org.opencontainers.image.source="https://github.com/shadmanArko/dk-intelligence" \
      org.opencontainers.image.revision="${GIT_SHA}"
# openssh-client: door 1 (the code opens its own warehouse tunnel) and door 2 (rsync over ssh); tini: PID 1 that
# forwards systemd's SIGTERM so a stopped run still writes its report.
RUN apt-get update \
 && apt-get install -y --only-upgrade apt zlib1g \
 && apt-get install -y --no-install-recommends openssh-client rsync tini \
 && rm -rf /var/lib/apt/lists/*
# /mip = the data platform as the code expects it (MIP_DIR): its checkout (read-only mount /mip-repo) and its data
# (read-only mount /mip-data: the weekly status file, bulk wikidata).
RUN python -m pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3.13/ensurepip /root/.cache \
 && useradd --uid 10004 --create-home --shell /usr/sbin/nologin intel \
 && mkdir -p /data /mip /home/intel/.ssh && chown intel:intel /data /home/intel/.ssh && chmod 700 /home/intel/.ssh \
 && ln -s /mip-repo/config /mip/config && ln -s /mip-repo/docs /mip/docs && ln -s /mip-data /mip/data
COPY --from=builder /app/.venv /app/.venv
COPY deploy/server/entrypoint.sh deploy/server/run-weekly.sh deploy/server/rebase_mlflow.py /usr/local/lib/intel-server/
WORKDIR /app
USER intel
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/home/intel \
    INTEL_DATA_DIR=/data MIP_DIR=/mip MIP_STATUS_FILE=/mip/data/logs/weekly_last_status \
    INTEL_GIT_SHA=${GIT_SHA} MLFLOW_DISABLE_AGENT_HINT=1 GIT_PYTHON_REFRESH=quiet TZ=UTC \
    # read-only root file system: caches go to /tmp (tmpfs) or the data volume
    XDG_CACHE_HOME=/tmp/cache MPLCONFIGDIR=/tmp/mpl HF_HOME=/data/hf-cache
ENTRYPOINT ["/usr/bin/tini", "--", "/bin/sh", "/usr/local/lib/intel-server/entrypoint.sh"]
CMD ["/bin/sh", "/usr/local/lib/intel-server/run-weekly.sh"]
