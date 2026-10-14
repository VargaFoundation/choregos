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

# rtk (ADR 0055): the version and its digests come from `versions.lock`. `sha256sum -c` fails the
# build on a wrong digest. amd64 takes the static release binary. arm64 builds the same release from
# source, because upstream's aarch64 binary needs glibc 2.39 and bookworm has 2.36. The stage runs
# on the build platform: an LTO build of rtk takes over ten minutes natively, and the main CI builds
# arm64 under QEMU. So arm64 is cross-compiled against bookworm's glibc. The runtime stage below
# runs the result. BuildKit sets `BUILDPLATFORM`. The legacy builder (no buildx) does not, and
# falls back to an amd64 build host.
FROM --platform=${BUILDPLATFORM:-linux/amd64} rust:1.91-slim-bookworm AS rtk
ARG TARGETARCH
COPY packages/runner/src/choregos_runner/backends/versions.lock /tmp/versions.lock
WORKDIR /src
RUN set -eux; \
    version() { grep "^$1=" /tmp/versions.lock | cut -d= -f2; }; \
    release="https://github.com/rtk-ai/rtk"; \
    rtk="$(version rtk)"; \
    host="$(dpkg --print-architecture)"; \
    apt-get update && apt-get install -y --no-install-recommends curl ca-certificates; \
    case "${TARGETARCH:-${host}}" in \
      amd64) \
        curl -fsSL -o rtk.tar.gz "${release}/releases/download/v${rtk}/rtk-x86_64-unknown-linux-musl.tar.gz"; \
        echo "$(version rtk-sha256-x86_64-unknown-linux-musl)  rtk.tar.gz" | sha256sum -c -; \
        tar xzf rtk.tar.gz rtk; \
        install -m 0755 rtk /usr/local/bin/rtk ;; \
      arm64) \
        curl -fsSL -o rtk.tar.gz "${release}/archive/refs/tags/v${rtk}.tar.gz"; \
        echo "$(version rtk-sha256-source)  rtk.tar.gz" | sha256sum -c -; \
        tar xzf rtk.tar.gz --strip-components=1; \
        if [ "${host}" != arm64 ]; then \
          apt-get install -y --no-install-recommends gcc-aarch64-linux-gnu libc6-dev-arm64-cross; \
          rustup target add aarch64-unknown-linux-gnu; \
          export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
                 CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc; \
        fi; \
        cargo build --release --locked --target aarch64-unknown-linux-gnu; \
        install -m 0755 target/aarch64-unknown-linux-gnu/release/rtk /usr/local/bin/rtk ;; \
      *) echo "no rtk for ${TARGETARCH}" >&2; exit 1 ;; \
    esac

# headroom (ADR 0055): its own venv, so its dependency tree (litellm, onnxruntime, transformers)
# never meets the runner's. `--require-hashes` checks every wheel against
# `docker/headroom/requirements.txt` (`make headroom-lock`). The Kompress model is fetched here, at
# the revision headroom pins, then loaded again with the network forbidden: a run never downloads it.
FROM python:3.12-slim-bookworm AS headroom
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 HF_HOME=/opt/headroom/models
COPY docker/headroom/requirements.txt /tmp/headroom-requirements.txt
RUN uv venv /opt/headroom --python /usr/local/bin/python3.12 \
 && VIRTUAL_ENV=/opt/headroom uv pip install --require-hashes --no-deps \
      -r /tmp/headroom-requirements.txt
RUN set -eux; \
    warm='import sys; from headroom.transforms.kompress_compressor import warm_kompress_model as w; sys.exit(0 if w(allow_download=sys.argv[1] == "online") else 1)'; \
    /opt/headroom/bin/python -c "$warm" online; \
    HEADROOM_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /opt/headroom/bin/python -c "$warm" offline

FROM python:3.12-slim-bookworm AS runtime
ARG NODE_VERSION=22
# `RTK_TELEMETRY_DISABLED`: rtk carries an HTTP client for its usage telemetry, and a runner
# reports to nobody but Choregos. `HEADROOM_OFFLINE`: headroom's one switch for all its egress
# (beacon, update check, usage reporter, model downloads); its models are under `HF_HOME`. It does
# not cover the litellm it imports, which fetches its price list from GitHub at every start:
# `LITELLM_LOCAL_MODEL_COST_MAP` makes it read the copy in its own package.
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/usr/local/bin:$PATH" \
    HOME=/workspace \
    GIT_TERMINAL_PROMPT=0 \
    RTK_TELEMETRY_DISABLED=1 \
    HEADROOM_OFFLINE=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    HF_HOME=/opt/headroom/models

# Outils du travail d'un développeur : git, gh, jq, ripgrep, make, compilateurs usuels. Les
# correctifs de sécurité de Debian d'abord : l'image de base peut porter un paquet déjà corrigé.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git gnupg jq ripgrep make build-essential tini unzip nginx \
 && curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g npm@latest pnpm@latest \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# Scanners de sécurité, utilisés par la boucle DoD et la CI des projets.
COPY --from=aquasec/trivy:0.74.0 /usr/local/bin/trivy /usr/local/bin/trivy
COPY --from=zricethezav/gitleaks:v8.30.1 /usr/bin/gitleaks /usr/local/bin/gitleaks
# rtk is installed but switched off: only a policy turns it on for a run (ADR 0055).
COPY --from=rtk /usr/local/bin/rtk /usr/local/bin/rtk
# headroom too: only a policy starts it, on loopback, for one run (ADR 0055). Its venv stays off
# the `PATH`, so its packages never shadow the runner's or a project's.
COPY --from=headroom /opt/headroom /opt/headroom
RUN ln -s /opt/headroom/bin/headroom /usr/local/bin/headroom

# Agents ACP, à versions épinglées (packages/runner/backends/versions.lock).
COPY packages/runner/src/choregos_runner/backends/versions.lock /etc/choregos/versions.lock
RUN set -eux; \
    version() { grep "^$1=" /etc/choregos/versions.lock | cut -d= -f2; }; \
    npm install -g --allow-scripts=opencode-ai,@github/keytar,node-pty \
      "@zed-industries/claude-code-acp@$(version claude-agent-acp)" \
      "@google/gemini-cli@$(version gemini-cli)" \
      "opencode-ai@$(version opencode)"

# OpenHands n'est plus installé (docs/adr/0011-openhands-removed.md). Il n'exposait aucun
# agent ACP en ligne de commande, et il pesait le plus lourd de l'image : son arbre Python
# portait quatre des quatorze CRITICAL trouvés par Trivy (`litellm`, `fastmcp`, `anyio`,
# `GitPython`), et c'était lui qui rendait la construction arm64 interminable.

# Ce que l'image prétend contenir, elle le contient — et ce qu'elle contient démarre.
#
# Le `|| echo` qui suivait le `npm install` a laissé publier une image runner sans `opencode` :
# la version épinglée n'existait pas sur npm, l'installation sortait en erreur, l'avertissement
# partait dans un log de build que personne ne lit, et l'image était signée comme les autres.
#
# Et `command -v` ne suffit pas. `opencode-ai` 1.x installe un lanceur qui télécharge son vrai
# binaire en postinstall, et npm 12 bloque les scripts de cycle de vie par défaut — une
# nouveauté héritée en montant npm pour le CVE de son `tar`. Résultat : le fichier existe,
# `command -v opencode` le trouve, et toute invocation répond « opencode-ai's postinstall
# script was not run ». D'où `--allow-scripts` ci-dessus, et un contrôle qui **appelle** chaque
# agent au lieu de constater sa présence.
RUN set -eux; \
    timeout 30 claude-code-acp --version >/dev/null </dev/null; \
    timeout 30 gemini --version >/dev/null; \
    timeout 30 opencode --version >/dev/null; \
    timeout 30 rtk --version >/dev/null; \
    timeout 60 headroom --version >/dev/null

# Mutation testing, for the `qa` agent of the catalogue (S33-06): a test that still passes with the
# code broken proves nothing. mutmut for Python, Stryker for JavaScript and TypeScript; pinned, and
# CALLED, like the agents above. A project with its own Python environment runs
# `uv run --with mutmut mutmut run`, documented in the agent's instructions.
ARG MUTMUT_VERSION=3.7.0
ARG STRYKER_VERSION=10.0.0
RUN set -eux; \
    pip install --no-cache-dir "mutmut==${MUTMUT_VERSION}"; \
    npm install -g "@stryker-mutator/core@${STRYKER_VERSION}"; \
    timeout 30 mutmut --version >/dev/null; \
    timeout 60 stryker --version >/dev/null

# Shims : défense en profondeur. Aucun credential n'existe de toute façon, mais un agent
# qui essaie doit être **refusé et tracé**, pas silencieusement ignoré.
COPY docker/shims/refuse.sh /usr/local/bin/choregos-refuse
RUN chmod +x /usr/local/bin/choregos-refuse \
 && for binary in kubectl helm terraform az gcloud aws docker podman; do \
      ln -sf /usr/local/bin/choregos-refuse /usr/local/bin/$binary; \
    done

RUN useradd --uid 1000 --create-home --home-dir /workspace --shell /bin/bash choregos \
 && mkdir -p /workspace && chown -R 1000:1000 /workspace /etc/choregos

# nginx, for `validate_nginx_config` only (ADR 0052): `nginx -t` parses a configuration of the
# workspace and stops; nothing is started, nothing listens. It runs as uid 1000, so the paths a
# configuration usually names for its logs and temporary files (Debian compiles in
# `/var/log/nginx/access.log`) must be writable by it; the tool moves the pid file itself.
RUN mkdir -p /var/log/nginx /var/lib/nginx \
 && chown -R 1000:1000 /var/log/nginx /var/lib/nginx \
 && nginx -v

WORKDIR /app
COPY --from=builder --chown=1000:1000 /app /app
# Le runner doit DÉMARRER dans cette image. `uv sync --package choregos-runner` n'installe
# que ce que ce paquet déclare, quand l'environnement de développement en a bien plus : une
# dépendance oubliée ne se voyait qu'au premier agent lancé en cluster, sur un
# `ModuleNotFoundError` (arrivé avec `pydantic-settings`). Ici, la construction échoue.
RUN /app/.venv/bin/choregos-runner --help >/dev/null
WORKDIR /workspace
USER 1000
ENTRYPOINT ["/usr/bin/tini", "--", "choregos-runner"]
CMD ["run"]
