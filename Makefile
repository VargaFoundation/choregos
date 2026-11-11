# Choregos — monorepo commands (see AGENTS.md)
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv
KIND_CONFIG ?= dev/kind.yaml
# The pnpm version is READ from `apps/web/package.json`, never copied. The previous comment
# already said "it is the one in package.json" above a hand-written 9.15.0, left behind when the
# repository moved to pnpm 12: `make web-install` failed on "packages field missing or empty",
# because pnpm 9 reads `pnpm-workspace.yaml` as a workspace definition where pnpm 12 puts
# `allowBuilds` in it. CI copies nothing (`package_json_file`), which is why it worked while the
# Makefile did not.
PNPM_VERSION ?= $(shell python3 -c "import json;print(json.load(open('apps/web/package.json'))['packageManager'].split('@')[1])")
PNPM ?= corepack pnpm@$(PNPM_VERSION)
PY_PATHS := packages apps/api apps/orchestrator tools tests

.PHONY: help
help:  ## Print this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ───────────────────────── foundation ─────────────────────────
.PHONY: setup
setup:  ## Install the dependencies (Python + web)
	$(UV) sync
	@command -v $(PNPM) >/dev/null 2>&1 || corepack enable pnpm
	@[ -d apps/web ] && $(PNPM) -C apps/web install --prefer-offline || true

.PHONY: ci
ci: lint typecheck test-cov contracts-check procedures-check charts-lint  ## Everything that blocks a PR

.PHONY: lint
lint:  ## ruff check + format --check + no French in lines added since origin/main (ADR 0046) + the French ratchet (ADR 0098)
	$(UV) run ruff check $(PY_PATHS)
	$(UV) run ruff format --check $(PY_PATHS)
	$(UV) run python tools/check_english.py
	$(UV) run python tools/check_english.py --all
	$(UV) run python -m tools.catalogues.build_mcp_catalogue --check
	$(UV) run python -m tools.catalogues.build_skill_catalogue --check

.PHONY: english-ratchet
english-ratchet:  ## Every file at or below its French baseline (ADR 0098)
	$(UV) run python tools/check_english.py --all

.PHONY: format
format:  ## Format the Python code
	$(UV) run ruff format $(PY_PATHS)
	$(UV) run ruff check --fix $(PY_PATHS)

.PHONY: typecheck
typecheck:  ## mypy --strict over packages and apps
	$(UV) run mypy packages/*/src apps/api/src apps/orchestrator/src tools

.PHONY: test
test:  ## Unit tests
	$(UV) run pytest -q

.PHONY: test-cov
test-cov:  ## Tests with coverage over the packages in pyproject's coverage source; the threshold is fail_under
	$(UV) run pytest --cov --cov-report=term-missing --cov-report=xml -q

.PHONY: contracts
contracts:  ## Regenerate the types from packages/contracts (to commit)
	$(UV) run python tools/gen_contracts.py

.PHONY: catalogues
catalogues:  ## Regenerates the shipped catalogues from the recorded sources, no network (ADR 0065)
	$(UV) run python -m tools.catalogues.build_mcp_catalogue
	$(UV) run python -m tools.catalogues.build_skill_catalogue

.PHONY: catalogues-check
catalogues-check:  ## Fails if a shipped catalogue differs from what its recording yields
	$(UV) run python -m tools.catalogues.build_mcp_catalogue --check
	$(UV) run python -m tools.catalogues.build_skill_catalogue --check

.PHONY: catalogues-record
catalogues-record:  ## A maintainer, WITH network and GITHUB_TOKEN: re-record the sources, then regenerate
	$(UV) run python -m tools.catalogues.build_mcp_catalogue --record
	$(UV) run python -m tools.catalogues.build_skill_catalogue --record

.PHONY: plugins-lock
plugins-lock:  ## A maintainer, WITH network: refresh plugins/offline/ and its plugins.lock from the signed source
	$(UV) run python -m tools.plugins.update_lock

.PHONY: plugins-check
plugins-check:  ## No network: the archives of plugins/offline/ are what plugins.lock says
	$(UV) run python -m tools.plugins.update_lock --check

.PHONY: docs-cli
docs-cli:  ## Regenerate docs/cli.md and docs/site/cli/reference.md from the CLI (to commit)
	$(UV) run python tools/gen_cli_reference.py

# The native CLI binary of this machine (S62-07, `tools/cli_binary/`): on Linux in the manylinux_2_28
# image (docker), as the release builds it; elsewhere with this machine's compiler.
CLI_BINARY_FLAGS ?= $(if $(filter Linux,$(shell uname -s)),--manylinux,)

.PHONY: cli-binary
cli-binary:  ## Build the native `choregos` binary of this machine into dist/cli/ (Nuitka)
	python3 tools/cli_binary/build.py $(CLI_BINARY_FLAGS)

.PHONY: cli-binary-smoke
cli-binary-smoke:  ## Smoke-test it where no Python is available (Linux: also in debian:bookworm-slim)
	python3 tools/cli_binary/smoke.py dist/cli/$$(python3 -c "import sys; sys.path.insert(0, 'tools/cli_binary'); import build; print(build.file_name(*build.host_target()))") \
	  --expect-version $$(sed -n 's/^version = "\(.*\)"$$/\1/p' packages/cli/pyproject.toml) \
	  $(if $(filter Linux,$(shell uname -s)),--container debian:bookworm-slim,)

.PHONY: headroom-lock
# certifi is exempt from the 14 days: 2026.10.19 removes root CAs with compromised keys
# (GHSA-6q48-6c7w-9gw2, CRITICAL, #856), and Trivy refuses the image without it. The exemption
# carries no date: a dated one would hold certifi at that day and block the next CA revocation.
headroom-lock:  ## Recompile docker/headroom/requirements.txt (hashes, releases at least 14 days old)
	$(UV) pip compile docker/headroom/requirements.in --universal --python-version 3.12 \
	  --generate-hashes --exclude-newer P14D --exclude-newer-package certifi=false \
	  -o docker/headroom/requirements.txt

.PHONY: laya-lock
laya-lock:  ## Recompile docker/laya/requirements.txt (hashes, CPU-only torch, releases at least 14 days old)
	$(UV) pip compile docker/laya/requirements.in --universal --python-version 3.12 --torch-backend cpu \
	  --generate-hashes --exclude-newer P14D --exclude-newer-package certifi=false \
	  -o docker/laya/requirements.txt

.PHONY: procedures
procedures:  ## Regenerates docs/procedures/ from the setup procedures (to commit)
	$(UV) run python tools/procedures/export_docs.py

.PHONY: procedures-check
procedures-check:  ## Fails if docs/procedures/ differs from the setup procedures
	$(UV) run python tools/procedures/export_docs.py --check

.PHONY: contracts-check
contracts-check:  ## Check that the generated types are up to date
	$(UV) run python tools/gen_contracts.py --check

# ───────────────────────── web ─────────────────────────
.PHONY: web-install
web-install:
	$(PNPM) -C apps/web install --prefer-offline

.PHONY: web-lint
web-lint:  ## Front end lint
	$(PNPM) -C apps/web lint

.PHONY: web-typecheck
web-typecheck:  ## Front end typecheck
	$(PNPM) -C apps/web typecheck

.PHONY: web-test
web-test:  ## Front end unit tests
	$(PNPM) -C apps/web test

.PHONY: web-build
web-build:  ## Front end production build
	$(PNPM) -C apps/web build

.PHONY: web-ci
web-ci: web-lint web-typecheck web-test web-build  ## Front end CI

# ───────────────────────── charts ─────────────────────────
.PHONY: charts-lint
charts-lint:  ## helm lint + kubeconform (skipped if helm is missing)
	@command -v helm >/dev/null 2>&1 || { echo "helm is missing — step skipped"; exit 0; }
	helm lint charts/choregos --set choregos-orchestrator.runner.skipOperatorCheck=true
	@command -v kubeconform >/dev/null 2>&1 \
		&& helm template choregos charts/choregos --set choregos-orchestrator.runner.skipOperatorCheck=true | kubeconform -strict -ignore-missing-schemas -summary \
		|| echo "kubeconform is missing — schema validation skipped"

.PHONY: charts-test
charts-test:  ## helm unittest
	@command -v helm >/dev/null 2>&1 || { echo "helm is missing — install Helm >= 3.18"; exit 1; }
	@# The plugin version is PINNED, and it must be checked, not just its presence:
	@# `|| install` only installs when there is none, so an old plugin stayed in place
	@# and gave a wrong verdict. Seen on 2026-09-26: 0.5.1 on this machine reported red
	@# a test that was green in CI (it treats a missing JSONPath as an error). A test whose
	@# verdict depends on the machine is worthless; better to refuse to run it.
	@v=$$(helm plugin list 2>/dev/null | awk '$$1=="unittest"{print $$2}'); \
	if [ "$$v" != "1.1.2" ]; then \
	  echo "helm-unittest plugin is \"$${v:-missing}\", pinned to 1.1.2."; \
	  echo "  helm plugin uninstall unittest 2>/dev/null; rm -rf ~/.local/share/helm/plugins/helm-unittest"; \
	  echo "  helm plugin install https://github.com/helm-unittest/helm-unittest --version v1.1.2"; \
	  echo "It requires Helm >= 3.18 (platformHooks in its plugin.yaml); here: $$(helm version --short)."; \
	  echo "Meanwhile, tests/charts/ renders the chart and checks it in Python, without the plugin."; \
	  exit 1; \
	fi
	helm unittest charts/choregos

.PHONY: policies-test
policies-test:  ## The Kyverno engine judges infra/policies against infra/policies/tests
	@# Pinned and checked, like helm-unittest above: Kyverno CLI 1.13.4 counts "want fail, got
	@# pass" as a pass, so a policy weakened to admit `nginx` stayed green there.
	@v=$$(kyverno version 2>/dev/null | awk '/^Version:/{print $$2}'); \
	if [ "$$v" != "1.19.1" ]; then \
	  echo "kyverno CLI is $${v:-absent}, pinned to 1.19.1:"; \
	  echo "  https://github.com/kyverno/kyverno/releases/tag/v1.19.1 (kyverno-cli_v1.19.1_*)"; \
	  exit 1; \
	fi
	@# 1.19.1 also counts a resource no rule matched as a pass ("Excluded"): a "want fail" pod
	@# whose namespace drifted out of the match stayed green (#911). Every listed pod is matched.
	@out=$$(kyverno test infra/policies/tests 2>&1); status=$$?; echo "$$out"; \
	if [ $$status -ne 0 ]; then exit $$status; fi; \
	if echo "$$out" | grep -q 'Excluded'; then \
	  echo "✗ a tested pod is matched by no rule (Excluded): its verdict proves nothing"; exit 1; \
	fi

# ───────────────────────── images ─────────────────────────
# `--target` is not optional on `api.Dockerfile`: without it, Docker builds the last stage of
# the file, which is `worker`.
.PHONY: images
images:  ## Build the platform's five images locally (tag `:dev`)
	docker build -f docker/api.Dockerfile    --target api    -t choregos-api:dev .
	docker build -f docker/api.Dockerfile    --target worker -t choregos-orchestrator:dev .
	docker build -f docker/tools.Dockerfile                  -t choregos-tools:dev .
	docker build -f docker/web.Dockerfile                    -t choregos-web:dev .
	docker build -f docker/runner.Dockerfile                 -t choregos-runner:dev .

# Archives the workflow histories of a reachable Temporal (port-forward of the test bench) so
# that `tests/replay` replays them in CI. Without a history, that test SKIPS — it did so for a
# week without anyone noticing.
TEMPORAL_ADDRESS ?= 127.0.0.1:7233
REPLAY_IDS ?=
.PHONY: replay-record
replay-record:  ## Archive Temporal histories into tests/replay/histories (REPLAY_IDS=… or every interpreter)
	$(UV) run python tools/replay_record.py --address $(TEMPORAL_ADDRESS) $(if $(REPLAY_IDS),$(REPLAY_IDS),--all-interpreters)

# ───────────────────────── demonstration (single-node kind, real agents) ─────────────────────────
# The demonstration chart expects `local/choregos-*:demo` with `imagePullPolicy: Never`:
# the images must be built HERE and loaded into the kind node. Without this step, every pod
# stays in `ErrImageNeverPull` — which is what happened to anyone following the README before
# 2026-09-24, which did not mention it.
KIND_DEMO ?= choregos-demo
DEMO_NS ?= choregos
DEMO_IMAGES := local/choregos-api:demo local/choregos-orchestrator:demo local/choregos-runner:demo local/choregos-web:demo

.PHONY: demo-images
demo-images:  ## Build the demonstration images and load them into the `choregos-demo` kind cluster
	docker build -f docker/api.Dockerfile    --target api    -t local/choregos-api:demo .
	docker build -f docker/api.Dockerfile    --target worker -t local/choregos-orchestrator:demo .
	docker build -f docker/runner.Dockerfile                 -t local/choregos-runner:demo .
	docker build -f docker/web.Dockerfile                    -t local/choregos-web:demo .
	kind load docker-image --name $(KIND_DEMO) $(DEMO_IMAGES)

.PHONY: demo-up
demo-up:  ## kind cluster (if missing), namespace, git repository, ConfigMaps, chart
	kind get clusters | grep -qx $(KIND_DEMO) || kind create cluster --config demo/kind.yaml
	kubectl get ns $(DEMO_NS) >/dev/null 2>&1 || kubectl create ns $(DEMO_NS)
	kubectl -n $(DEMO_NS) apply -k demo
	helm upgrade --install choregos charts/choregos -n $(DEMO_NS) \
	  -f charts/choregos/values/local.yaml -f demo/values-demo.yaml --wait --timeout 10m

# `SERIE=b` suffixes the keys (`DEMO-1b`, `RH-1b`): a ticket has only one interpreter, so a
# replay needs new tickets. `GATEWAY=direct` for a bench without a provider key.
SERIE ?=
GATEWAY ?= litellm
.PHONY: demo-seed
demo-seed:  ## Lay down (or complete) the projects and tickets, and start each ticket
	kubectl -n $(DEMO_NS) apply -k demo
	kubectl -n $(DEMO_NS) delete job demo-seed --ignore-not-found
	sed -e 's/__SERIE__/$(SERIE)/' -e 's/__GATEWAY__/$(GATEWAY)/' demo/manifests/seed-job.yaml | kubectl -n $(DEMO_NS) apply -f -
	kubectl -n $(DEMO_NS) wait --for=condition=complete job/demo-seed --timeout=180s
	kubectl -n $(DEMO_NS) logs job/demo-seed

.PHONY: demo-status
demo-status:  ## Where the tickets, the runs and the cost ledger stand
	@kubectl -n $(DEMO_NS) get pods -l choregos/run-id 2>/dev/null || true
	@kubectl -n $(DEMO_NS) exec choregos-postgresql-0 -- sh -c 'psql -U "$${POSTGRES_USER:-choregos}" -d "$${POSTGRES_DB:-choregos}" \
	  -c "select tracker_key, state from work_items order by 1" \
	  -c "select kind, count(*), round(sum(cost_eur)::numeric, 4) as eur, sum(tokens_in) tin, sum(tokens_out) tout from cost_ledger group by 1"'

# ───────────────────────── dev ─────────────────────────
.PHONY: dev-up
dev-up:  ## kind cluster + platform + Tilt
	dev/scripts/dev-up.sh

.PHONY: dev-down
dev-down:  ## Destroy the dev cluster
	dev/scripts/dev-down.sh

.PHONY: dev-seed
dev-seed:  ## Org, demo project, tickets, train
	$(UV) run python dev/scripts/seed.py

.PHONY: compose-up
compose-up:  ## Dependencies in docker compose (no Kubernetes)
	docker compose -f dev/compose.yaml up -d

.PHONY: compose-down
compose-down:
	docker compose -f dev/compose.yaml down -v

.PHONY: demo
demo:  ## End-to-end scenario in memory (fakes, no cluster)
	CHOREGOS_FAKES=1 $(UV) run python -m choregos_orchestrator.demo

.PHONY: demo-investment
demo-investment:  ## Investment campaigns on fakes with the real runner and engine: adopted, not relevant, refused memo (S38-09)
	$(UV) run python -m pytest -q -p no:warnings tests/e2e/test_m7_investment.py

.PHONY: api
api:  ## Run the API locally (fakes)
	CHOREGOS_FAKES=1 $(UV) run uvicorn choregos_api.main:app --reload --port 8000

.PHONY: worker
worker:  ## Run the Temporal workers
	$(UV) run python -m choregos_orchestrator.worker --queues orchestrator,executor,tracker,memory

.PHONY: mock-api
mock-api:  ## Serve the mocked API from the OpenAPI (Prism)
	npx --yes @stoplight/prism-cli mock packages/contracts/openapi.yaml -p 4010

# ───────────────────────── e2e & evals ─────────────────────────
.PHONY: e2e
e2e:  ## End-to-end scenarios (kind required)
	$(UV) run pytest tests/e2e -m e2e -q

.PHONY: cluster-up
cluster-up:  ## kind cluster dedicated to tests (Calico: NetworkPolicies are enforced)
	@command -v kind >/dev/null || { echo "✗ kind is required (see docs/development.md)"; exit 1; }
	kind create cluster --config $(KIND_CONFIG) --wait 180s || true
	kubectl --context kind-choregos apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/calico.yaml
	@echo "· waiting for the CNI…"
	kubectl --context kind-choregos -n kube-system rollout status ds/calico-node --timeout=300s
	@echo "✓ cluster ready — CHOREGOS_CLUSTER_CONTEXT=kind-choregos make test-cluster"

.PHONY: cluster-gvisor
cluster-gvisor:  ## gVisor in the test cluster's runners node and the `gvisor` RuntimeClass (S41-17), after cluster-up
	dev/scripts/gvisor.sh kind

.PHONY: cluster-down
cluster-down:  ## Destroy the test cluster
	kind delete cluster --name choregos

.PHONY: test-cluster
test-cluster:  ## Tests that need a real cluster (S2-12, S7-04, S8-06)
	CHOREGOS_CLUSTER_CONTEXT=$${CHOREGOS_CLUSTER_CONTEXT:-kind-choregos} \
	  $(UV) run pytest tests/cluster -m cluster -q

.PHONY: test-live-local
test-live-local:  ## A work item through a REAL model on the local install (key in .env, Docker)
	@grep -qE '^(ANTHROPIC|OPENAI)_API_KEY=.+' .env 2>/dev/null || { echo "✗ put ANTHROPIC_API_KEY or OPENAI_API_KEY in .env (cp .env.example .env)"; exit 1; }
	LITELLM_REAL=1 docker compose up -d --build --wait
	python3 tools/local_install_check.py --expect-real --stage-timeout 900

.PHONY: test-live
test-live:  ## Tests against the real services (credentials in the environment)
	$(UV) run pytest tests/live -m live -q

.PHONY: conformance
conformance:  ## Conformance suites (ACP backends, templates)
	$(UV) run pytest tests/conformance -m conformance -q

.PHONY: conformance-agent
conformance-agent:  ## The 7 conformance checks of a code agent inside an image: IMAGE=<ref> COMMAND='["python","-m","my_agent"]'
	@test -n '$(IMAGE)' && test -n '$(COMMAND)' || { echo "usage: make conformance-agent IMAGE=<ref> COMMAND='[\"python\",\"-m\",\"my_agent\"]'"; exit 2; }
	CHOREGOS_CONFORMANCE_CODE_AGENTS='{"image": {"image": "$(IMAGE)", "command": $(COMMAND)}}' \
	  $(UV) run pytest tests/conformance/backends/test_code_agents.py -m conformance -q -k code_agent_passes

# A Node example serves `responses_server.mjs` (S41-20), the others `responses_server.py`, or
# `main.py` when they bring their own server (Foundry's hosting adapter, S41-19).
HOSTED_NODE_SERVER = ["$(or $(NODE),node)", "responses_server.mjs"]
HOSTED_PYTHON_SERVER = ["$(or $(PYTHON),python3)", "$(if $(wildcard examples/hosted/$(AGENT)/responses_server.py),responses_server.py,main.py)"]
HOSTED_SERVER = $(if $(wildcard examples/hosted/$(AGENT)/responses_server.mjs),$(HOSTED_NODE_SERVER),$(HOSTED_PYTHON_SERVER))

.PHONY: conformance-hosted
conformance-hosted:  ## The 8 checks of a hosted agent (ADR 0080): IMAGE=<ref>, or AGENT=<examples/hosted dir> [PYTHON=<interpreter>|NODE=<node>]
	@test -n '$(IMAGE)$(AGENT)' || { echo "usage: make conformance-hosted IMAGE=<ref> | AGENT=<dir of examples/hosted> [PYTHON=<interpreter>|NODE=<node>]"; exit 2; }
	CHOREGOS_CONFORMANCE_HOSTED_AGENTS='$(if $(IMAGE),{"image": {"image": "$(IMAGE)"}},{"$(AGENT)": {"command": $(HOSTED_SERVER), "cwd": "$(CURDIR)/examples/hosted/$(AGENT)"}})' \
	  $(UV) run pytest tests/conformance/hosted/test_hosted_agents.py -m conformance -q -k passes_every_check

.PHONY: evals
evals:  ## Evals of the playbooks and of the catalogue agents
	$(UV) run python -m choregos_playbooks.evals.runner --all
	$(UV) run python -m choregos_core.agent_catalogue.evals --all

.PHONY: clean
clean:  ## Clean the local artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
