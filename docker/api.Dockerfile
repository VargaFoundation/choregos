# API Choregos **et** workers Temporal : un seul fichier, deux cibles.
#
# Les deux images portent exactement le même jeu de dépendances et le même code — le worker
# n'est que l'API avec une autre commande. Les séparer en deux Dockerfiles avait un coût réel :
# `orchestrator.Dockerfile` partait de `choregos-api:latest`, c'est-à-dire d'une image qui
# n'existe pas au premier build et qui, ensuite, vient du commit **précédent** — les workers
# auraient embarqué le code d'hier sans que rien ne le signale. Deux cibles (`--target api`,
# `--target worker`) d'un même build règlent les deux.
FROM python:3.14-slim-bookworm AS builder

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
# The eval matrix's reference tickets and toy repositories (S12-01): `activities/evals.py` reads
# them from `/app/tests/fixtures`. The image did not carry them, and a live matrix on dev played
# nothing and published four empty cells (S37-13).
COPY tests/fixtures/tickets tests/fixtures/tickets
COPY tests/fixtures/repos tests/fixtures/repos
# `uv sync` n'accepte qu'un seul `--package` : deux occurrences font échouer la construction
# ("the argument '--package <PACKAGE>' cannot be used multiple times"). La cible `worker`
# partage ce builder, donc il doit porter les deux applications — on synchronise l'espace de
# travail entier, qui les contient et rien de plus lourd.
# The agent SDK (a package for agent authors: openai, the ACP SDK) is not something the API or the
# workers run: it stays out of the image.
RUN uv sync --frozen --no-dev --all-packages --no-install-package choregos-agent-sdk

# Socle commun aux deux cibles : rien qui décide de ce que le conteneur fait.
FROM python:3.14-slim-bookworm AS commun
# `PIP_PYTHON` : l'image de l'édition entreprise est construite FROM celle-ci et y ajoute sa roue
# (ADR 0024). Le venv, créé par uv, n'a pas de `pip` ; celui du PATH est celui du système. Sans
# cette ligne, `pip install --no-deps choregos_ee-*.whl` réussit, pose la roue dans `~/.local`,
# hors du venv — l'image se construit, démarre, et tourne en `community` sans rien dire. Essayé
# sur `choregos-api:0.8.3` le 2026-09-28 ; `tests/paquets/test_image_ee.py` le garde.
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" PIP_PYTHON=/app/.venv/bin/python
# Les correctifs de sécurité de Debian, appliqués à la construction : l'image de base (une étiquette,
# reconstruite à son rythme) peut porter un paquet déjà corrigé dans `bookworm-security` — perl-base
# 5.36.0-7+deb12u3 et ses trois CRITICAL ont arrêté la release 0.16.0 au scan Trivy.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin choregos
# The Hubble CLI, for the `cilium` connector's flows (ADR 0052): Relay speaks gRPC only, and
# `hubble observe -o jsonpb` is the client the Cilium project maintains. Pinned, checked against
# the release's checksum. The connector runs here (API and workers), never in the runner.
# v1.19.4 and not v1.17.0: the latter carried three CRITICAL CVEs (cilium, grpc, Go stdlib) that
# the image's Trivy gate refuses (#490). Any bump re-checks the `observe` flags the connector uses.
ARG HUBBLE_VERSION=v1.19.4
ARG TARGETARCH
ADD https://github.com/cilium/hubble/releases/download/${HUBBLE_VERSION}/hubble-linux-${TARGETARCH}.tar.gz /tmp/hubble.tar.gz
ADD https://github.com/cilium/hubble/releases/download/${HUBBLE_VERSION}/hubble-linux-${TARGETARCH}.tar.gz.sha256sum /tmp/hubble.sha256sum
RUN cd /tmp && echo "$(cut -d' ' -f1 hubble.sha256sum)  hubble.tar.gz" | sha256sum -c - \
 && tar -xzf hubble.tar.gz -C /usr/local/bin hubble \
 && rm -f hubble.tar.gz hubble.sha256sum \
 && hubble version
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

# --- Target `worker-local`: the workers of the local install (`compose.yaml`), with the Docker CLI.
#
# The local Docker executor (`executor/local_docker.py`) starts each run as a container by calling
# `docker`. Only the local install builds this target; the published `worker` stays without it.
# Defined BEFORE `worker`, so that a build without `--target` still produces `worker`.
FROM commun AS worker-local
COPY --from=docker:28.5.1-cli /usr/local/bin/docker /usr/local/bin/docker
CMD ["python", "-m", "choregos_orchestrator.worker", "--queues", "orchestrator,executor,tracker,memory"]

# --- Cible `worker` : les workers Temporal.
#
# Ni `EXPOSE` ni `HEALTHCHECK` : un worker n'écoute sur rien. L'ancienne image héritait de la
# sonde de l'API et interrogeait un port 8000 que personne ne servait — le conteneur se
# déclarait `unhealthy` en permanence, et tout ce qui lit ce drapeau (docker, compose, un
# ordonnanceur qui n'utilise pas les sondes Kubernetes) le croyait.
FROM commun AS worker
# `git`: a knowledge base synced from a repository fetches it here (S33-04, ADR 0051).
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
USER 1000
CMD ["python", "-m", "choregos_orchestrator.worker", "--queues", "orchestrator,executor,tracker,memory"]

