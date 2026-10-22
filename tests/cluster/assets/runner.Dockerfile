# The runner as the cluster tests need it (S41-24): the same venv as docker/runner.Dockerfile's
# builder stage, the same entrypoint and user, without the agent toolchain (Node, gh, scanners,
# rtk, headroom) that takes eight minutes to build and four gigabytes to load into kind.
# Not an image anything ships: `docker build -f tests/cluster/assets/runner.Dockerfile .`
FROM python:3.12-slim-bookworm AS builder
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY packages packages
COPY apps/api/pyproject.toml apps/api/
COPY apps/orchestrator/pyproject.toml apps/orchestrator/
RUN mkdir -p apps/api/src apps/orchestrator/src \
 && uv sync --frozen --no-dev --package choregos-runner

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --home-dir /workspace --shell /usr/sbin/nologin choregos \
 && mkdir /workspace && chown 1000:1000 /workspace
COPY --from=builder /app /app
ENV PYTHONUNBUFFERED=1 HOME=/workspace
WORKDIR /workspace
USER 1000
ENTRYPOINT ["/usr/bin/tini", "--", "/app/.venv/bin/choregos-runner"]
CMD ["run"]
