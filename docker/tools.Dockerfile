# Sidecar MCP `choregos-tools` : il ne détient que le jeton du run.
FROM python:3.14-slim-bookworm AS builder
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY packages packages
COPY apps/api/pyproject.toml apps/api/
COPY apps/orchestrator/pyproject.toml apps/orchestrator/
RUN mkdir -p apps/api/src apps/orchestrator/src \
 && uv sync --frozen --no-dev --package choregos-tools-mcp

FROM python:3.14-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" CHOREGOS_TOOLS_PORT=7777
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin choregos
WORKDIR /app
COPY --from=builder --chown=1000:1000 /app /app
USER 1000
EXPOSE 7777
HEALTHCHECK --interval=15s --timeout=3s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:7777/healthz')"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["choregos-tools"]
