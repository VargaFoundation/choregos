# API Choregos **et** workers Temporal : un seul fichier, deux cibles.
#
# Les deux images portent exactement le même jeu de dépendances et le même code — le worker
# n'est que l'API avec une autre commande. Les séparer en deux Dockerfiles avait un coût réel :
# `orchestrator.Dockerfile` partait de `choregos-api:latest`, c'est-à-dire d'une image qui
# n'existe pas au premier build et qui, ensuite, vient du commit **précédent** — les workers
# auraient embarqué le code d'hier sans que rien ne le signale. Deux cibles (`--target api`,
# `--target worker`) d'un même build règlent les deux.
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
# `/bin/sh` n'étend pas les accolades : elles créaient un répertoire nommé littéralement
# `packages/{contracts,core,…}`, que uv prenait ensuite pour un membre de l'espace de travail
# sans `pyproject.toml`.
RUN for p in contracts core adapters runner tools-mcp playbooks cli; do mkdir -p "packages/$p/src"; done \
 && for a in api orchestrator; do mkdir -p "apps/$a/src"; done

COPY packages packages
COPY apps/api apps/api
COPY apps/orchestrator apps/orchestrator
# Les templates de projet (manifest, scaffold) : le provisioning les lit à l'exécution.
# Ils manquaient à l'image — `provisioning.py` cherchait `/app/templates` qui n'existait
# pas, et aucun projet à template ne pouvait être provisionné depuis un pod (état des
# lieux du 2026-09-24, P1-4).
COPY templates templates
# `uv sync` n'accepte qu'un seul `--package` : deux occurrences font échouer la construction
# ("the argument '--package <PACKAGE>' cannot be used multiple times"). La cible `worker`
# partage ce builder, donc il doit porter les deux applications — on synchronise l'espace de
# travail entier, qui les contient et rien de plus lourd.
RUN uv sync --frozen --no-dev --all-packages

# Socle commun aux deux cibles : rien qui décide de ce que le conteneur fait.
FROM python:3.12-slim-bookworm AS commun
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin choregos
WORKDIR /app
COPY --from=builder --chown=1000:1000 /app /app
USER 1000
ENTRYPOINT ["/usr/bin/tini", "--"]

# --- Cible `api` : le service HTTP.
FROM commun AS api
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "choregos_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- Cible `worker` : les workers Temporal.
#
# Ni `EXPOSE` ni `HEALTHCHECK` : un worker n'écoute sur rien. L'ancienne image héritait de la
# sonde de l'API et interrogeait un port 8000 que personne ne servait — le conteneur se
# déclarait `unhealthy` en permanence, et tout ce qui lit ce drapeau (docker, compose, un
# ordonnanceur qui n'utilise pas les sondes Kubernetes) le croyait.
FROM commun AS worker
CMD ["python", "-m", "choregos_orchestrator.worker", "--queues", "orchestrator,executor,tracker,memory"]
