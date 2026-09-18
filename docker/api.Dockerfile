# API Choregos. Multi-stage : dépendances figées, puis image d'exécution minimale.
FROM python:3.12-slim-bookworm AS builder

ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
WORKDIR /app

# Les métadonnées d'abord : le cache de dépendances survit aux changements de code.
COPY pyproject.toml uv.lock README.md ./
COPY packages/contracts/pyproject.toml packages/contracts/
COPY packages/core/pyproject.toml packages/core/
COPY packages/adapters/pyproject.toml packages/adapters/
COPY packages/runner/pyproject.toml packages/runner/
COPY packages/tools-mcp/pyproject.toml packages/tools-mcp/
COPY packages/playbooks/pyproject.toml packages/playbooks/
COPY packages/cli/pyproject.toml packages/cli/
COPY apps/api/pyproject.toml apps/api/
COPY apps/orchestrator/pyproject.toml apps/orchestrator/
RUN mkdir -p packages/{contracts,core,adapters,runner,tools-mcp,playbooks,cli}/src \
             apps/{api,orchestrator}/src

COPY packages packages
COPY apps/api apps/api
COPY apps/orchestrator apps/orchestrator
RUN uv sync --frozen --no-dev --package choregos-api --package choregos-orchestrator

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin choregos
WORKDIR /app
COPY --from=builder --chown=1000:1000 /app /app
USER 1000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "choregos_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
