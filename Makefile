# Choregos — commandes du monorepo (voir AGENTS.md)
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv
# La version de pnpm est celle de `apps/web/package.json` : corepack la récupère,
# quelle que soit celle installée globalement (sinon ERR_PNPM_BAD_PM_VERSION).
PNPM_VERSION ?= 9.15.0
PNPM ?= corepack pnpm@$(PNPM_VERSION)
PY_PATHS := packages apps/api apps/orchestrator tools tests

.PHONY: help
help:  ## Affiche cette aide
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ───────────────────────── socle ─────────────────────────
.PHONY: setup
setup:  ## Installe les dépendances (Python + web)
	$(UV) sync
	@command -v $(PNPM) >/dev/null 2>&1 || corepack enable pnpm
	@[ -d apps/web ] && $(PNPM) -C apps/web install --prefer-offline || true

.PHONY: ci
ci: lint typecheck test contracts-check charts-lint  ## Tout ce qui bloque une PR

.PHONY: lint
lint:  ## ruff check + format --check
	$(UV) run ruff check $(PY_PATHS)
	$(UV) run ruff format --check $(PY_PATHS)

.PHONY: format
format:  ## Formate le code Python
	$(UV) run ruff format $(PY_PATHS)
	$(UV) run ruff check --fix $(PY_PATHS)

.PHONY: typecheck
typecheck:  ## mypy --strict sur packages et apps
	$(UV) run mypy packages/*/src apps/api/src apps/orchestrator/src tools

.PHONY: test
test:  ## Tests unitaires
	$(UV) run pytest -q

.PHONY: test-cov
test-cov:  ## Tests avec couverture (seuil 80 % sur core, runner, orchestrator)
	$(UV) run pytest --cov --cov-report=term-missing --cov-report=xml -q

.PHONY: contracts
contracts:  ## Régénère les types depuis packages/contracts (à committer)
	$(UV) run python tools/gen_contracts.py

.PHONY: contracts-check
contracts-check:  ## Vérifie que les types générés sont à jour
	$(UV) run python tools/gen_contracts.py --check

# ───────────────────────── web ─────────────────────────
.PHONY: web-install
web-install:
	$(PNPM) -C apps/web install --prefer-offline

.PHONY: web-lint
web-lint:  ## Lint du front
	$(PNPM) -C apps/web lint

.PHONY: web-typecheck
web-typecheck:  ## Typage du front
	$(PNPM) -C apps/web typecheck

.PHONY: web-test
web-test:  ## Tests unitaires du front
	$(PNPM) -C apps/web test

.PHONY: web-build
web-build:  ## Build de production du front
	$(PNPM) -C apps/web build

.PHONY: web-ci
web-ci: web-lint web-typecheck web-test web-build  ## CI du front

# ───────────────────────── charts ─────────────────────────
.PHONY: charts-lint
charts-lint:  ## helm lint + kubeconform (ignoré si helm absent)
	@command -v helm >/dev/null 2>&1 || { echo "helm absent — étape ignorée"; exit 0; }
	helm lint charts/choregos
	@command -v kubeconform >/dev/null 2>&1 \
		&& helm template choregos charts/choregos | kubeconform -strict -ignore-missing-schemas -summary \
		|| echo "kubeconform absent — validation de schéma ignorée"

.PHONY: charts-test
charts-test:  ## helm unittest
	@command -v helm >/dev/null 2>&1 || { echo "helm absent — installer Helm >= 3.18"; exit 1; }
	@helm plugin list 2>/dev/null | grep -q "^unittest" || helm plugin install https://github.com/helm-unittest/helm-unittest --version v1.1.2
	helm unittest charts/choregos

# ───────────────────────── images ─────────────────────────
# `--target` n'est pas optionnel sur `api.Dockerfile` : sans lui, Docker construit la dernière
# étape du fichier, qui est `worker`.
.PHONY: images
images:  ## Construit les cinq images de la plateforme en local (tag `:dev`)
	docker build -f docker/api.Dockerfile    --target api    -t choregos-api:dev .
	docker build -f docker/api.Dockerfile    --target worker -t choregos-orchestrator:dev .
	docker build -f docker/tools.Dockerfile                  -t choregos-tools:dev .
	docker build -f docker/web.Dockerfile                    -t choregos-web:dev .
	docker build -f docker/runner.Dockerfile                 -t choregos-runner:dev .

# ───────────────────────── dev ─────────────────────────
.PHONY: dev-up
dev-up:  ## Cluster kind + plateforme + Tilt
	dev/scripts/dev-up.sh

.PHONY: dev-down
dev-down:  ## Détruit le cluster de dev
	dev/scripts/dev-down.sh

.PHONY: dev-seed
dev-seed:  ## Org, projet démo, tickets, train
	$(UV) run python dev/scripts/seed.py

.PHONY: compose-up
compose-up:  ## Dépendances en docker compose (sans Kubernetes)
	docker compose -f dev/compose.yaml up -d

.PHONY: compose-down
compose-down:
	docker compose -f dev/compose.yaml down -v

.PHONY: demo
demo:  ## Scénario bout en bout en mémoire (fakes, sans cluster)
	CHOREGOS_FAKES=1 $(UV) run python -m choregos_orchestrator.demo

.PHONY: api
api:  ## Lance l'API en local (fakes)
	CHOREGOS_FAKES=1 $(UV) run uvicorn choregos_api.main:app --reload --port 8000

.PHONY: worker
worker:  ## Lance les workers Temporal
	$(UV) run python -m choregos_orchestrator.worker --queues orchestrator,executor,tracker,memory

.PHONY: mock-api
mock-api:  ## Sert l'API mockée depuis l'OpenAPI (Prism)
	npx --yes @stoplight/prism-cli mock packages/contracts/openapi.yaml -p 4010

# ───────────────────────── e2e & évals ─────────────────────────
.PHONY: e2e
e2e:  ## Scénarios bout en bout (kind requis)
	$(UV) run pytest tests/e2e -m e2e -q

.PHONY: cluster-up
cluster-up:  ## Cluster kind dédié aux tests (Calico : les NetworkPolicy sont appliquées)
	@command -v kind >/dev/null || { echo "✗ kind est requis (voir docs/dev.md)"; exit 1; }
	kind create cluster --config dev/kind.yaml --wait 180s || true
	kubectl --context kind-choregos apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/calico.yaml
	@echo "· attente du CNI…"
	kubectl --context kind-choregos -n kube-system rollout status ds/calico-node --timeout=300s
	@echo "✓ cluster prêt — CHOREGOS_CLUSTER_CONTEXT=kind-choregos make test-cluster"

.PHONY: cluster-down
cluster-down:  ## Détruit le cluster de test
	kind delete cluster --name choregos

.PHONY: test-cluster
test-cluster:  ## Tests qui exigent un vrai cluster (S2-12, S7-04, S8-06)
	CHOREGOS_CLUSTER_CONTEXT=$${CHOREGOS_CLUSTER_CONTEXT:-kind-choregos} \
	  $(UV) run pytest tests/cluster -m cluster -q

.PHONY: test-live
test-live:  ## Tests contre les vrais services (identifiants dans l'environnement)
	$(UV) run pytest tests/live -m live -q

.PHONY: conformance
conformance:  ## Suites de conformité (backends ACP, templates)
	$(UV) run pytest tests/conformance -m conformance -q

.PHONY: evals
evals:  ## Évals des playbooks
	$(UV) run python -m choregos_playbooks.evals.runner --all

.PHONY: clean
clean:  ## Nettoie les artefacts locaux
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
