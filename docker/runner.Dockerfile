# Image du runner : le workspace d'un agent.
#
# Trois principes :
#   1. **aucun credential** — ni kubectl, ni cloud, ni registre ; les shims refusent et journalisent ;
#   2. **versions épinglées** — les agents ACP viennent de `versions.lock`, Renovate propose
#      les montées, la suite de conformité les valide ;
#   3. **non-root, sans capacité** — le sandbox du cluster fait le reste.
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

FROM python:3.12-slim-bookworm AS runtime
ARG NODE_VERSION=22
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/usr/local/bin:$PATH" \
    HOME=/workspace \
    GIT_TERMINAL_PROMPT=0

# Outils du travail d'un développeur : git, gh, jq, ripgrep, make, compilateurs usuels.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git gnupg jq ripgrep make build-essential tini unzip \
 && curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g pnpm@9 \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# Scanners de sécurité, utilisés par la boucle DoD et la CI des projets.
COPY --from=aquasec/trivy:0.58.1 /usr/local/bin/trivy /usr/local/bin/trivy
COPY --from=zricethezav/gitleaks:v8.22.1 /usr/bin/gitleaks /usr/local/bin/gitleaks

# Agents ACP, à versions épinglées (packages/runner/backends/versions.lock).
COPY packages/runner/src/choregos_runner/backends/versions.lock /etc/choregos/versions.lock
RUN set -eux; \
    version() { grep "^$1=" /etc/choregos/versions.lock | cut -d= -f2; }; \
    npm install -g \
      "@zed-industries/claude-code-acp@$(version claude-agent-acp)" \
      "@google/gemini-cli@$(version gemini-cli)" \
      "opencode-ai@$(version opencode)" || echo "⚠ certains agents npm n'ont pas pu être installés"; \
    pip install --no-cache-dir "openhands-ai==$(version openhands)" || \
      echo "⚠ OpenHands non installé dans cette image (voir la variante `full`)"

# Shims : défense en profondeur. Aucun credential n'existe de toute façon, mais un agent
# qui essaie doit être **refusé et tracé**, pas silencieusement ignoré.
COPY docker/shims/refuse.sh /usr/local/bin/choregos-refuse
RUN chmod +x /usr/local/bin/choregos-refuse \
 && for binary in kubectl helm terraform az gcloud aws docker podman; do \
      ln -sf /usr/local/bin/choregos-refuse /usr/local/bin/$binary; \
    done

RUN useradd --uid 1000 --create-home --home-dir /workspace --shell /bin/bash choregos \
 && mkdir -p /workspace && chown -R 1000:1000 /workspace /etc/choregos

WORKDIR /app
COPY --from=builder --chown=1000:1000 /app /app
WORKDIR /workspace
USER 1000
ENTRYPOINT ["/usr/bin/tini", "--", "choregos-runner"]
CMD ["run"]
