# Choregos — instructions pour les agents

## Contexte
Plateforme de delivery agentique (Varga Foundation, Apache 2.0). Plan complet : `docs/plan/`. Contrats : `packages/contracts` (ne pas modifier sans PR `contract-change`).

## Commandes
- Tout : `make ci` (lint + typecheck + tests unitaires) · `make dev-up` / `make dev-down` (kind + Tilt) · `make e2e`
- Python (uv) : `uv run ruff check . && uv run ruff format --check . && uv run mypy packages apps && uv run pytest -q`
- Web : `pnpm -C apps/web lint && pnpm -C apps/web typecheck && pnpm -C apps/web test && pnpm -C apps/web build`
- Charts : `helm lint charts/choregos && helm template charts/choregos | kubeconform -strict`
- Contrats : `make contracts` (régénère les types ; committer le résultat)

## Conventions
- Python 3.12, typage strict (`mypy --strict` sur `packages/*`), pydantic v2, async partout côté I/O, structlog. TypeScript strict, ESLint, Prettier.
- Commits conventionnels : `feat(orchestrator): …`, `fix(runner): …`, `contract(schemas): …`. Une story = une PR = un squash.
- Tests : unitaires obligatoires ; intégration avec fakes (`CHOREGOS_FAKES=1`) ; e2e sur kind pour les jalons. Couverture ≥ 80 % sur `packages/core`, `packages/runner`, `apps/orchestrator`.
- Idempotence : toute activité Temporal et tout step de provisioning est rejouable sans effet double.
- Sécurité : aucun secret en dur ; jamais de token large dans un runner ; toute écriture tracker via l'API interne.
- Journaux : JSON, clés `project`, `work_item`, `run_id`, `stage` quand disponibles.

## Definition of done (bloquante)
Tests verts (`make ci`), typage vert, lint vert, docs mises à jour (`docs/` ou docstrings), critères d'acceptation de la story cochés dans la PR, pas de `TODO` sans issue liée, `docs/plan/STATUS.md` mis à jour par l'intégrateur.

## Hors périmètre
Si tu découvres un problème hors de ta story : n'y touche pas, ouvre une issue `finding` avec `origin: <story>`, preuve et proposition. Si tu as besoin d'un contrat : issue `contract-change`.

## Mémoire
Les décisions d'architecture sont dans `docs/plan/` et `docs/adr/`. Lis-les avant de décider ; ajoute un ADR (`docs/adr/NNNN-*.md`) pour toute décision structurante.
