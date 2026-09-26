# Choregos — instructions pour les agents

> Ce fichier a été **vidé par accident** le 2026-09-24 (commit `98c2a492`) : la bascule de la
> documentation en anglais devait faire de `CLAUDE.md` un pointeur vers `AGENTS.md`, et a remplacé
> le contenu d'`AGENTS.md` par un import de lui-même. `CLAUDE.md` est un lien symbolique vers ce
> fichier ; les deux sont donc la même page, et `tests/docs/test_instructions_des_agents.py` refuse
> qu'elle se re-vide.

## Contexte
Plateforme de delivery agentique (Varga Foundation, Apache 2.0). Plan et état des lieux :
`docs/plan/`. Documentation de référence : `docs/`. Décisions structurantes : `docs/adr/`.
Contrats : `packages/contracts` — **ne pas modifier sans PR `contract-change`** (ADR 0001).

## Langues
La documentation de référence est en **anglais** (`docs/`) ; `docs/fr/` est une archive non
maintenue. Le **français** reste la langue du code, des commentaires, des docstrings, de
`docs/plan/` (STATUS, BLOCKERS) et des messages de commit.

## Commandes (celles de la CI, pas d'autres)
- Tout ce qui bloque une PR : `make ci` = `lint` + `typecheck` + `test` + `contracts-check` + `charts-lint`.
- Python : `uv run ruff check packages apps/api apps/orchestrator tools tests` puis
  `ruff format --check` sur les mêmes chemins — **`tests/` en fait partie**, un E501 y casse la CI.
- Typage : `uv run mypy packages/*/src apps/api/src apps/orchestrator/src tools` (strict).
- Tests : `uv run pytest -q` ; `-m "not slow"` écarte les lents (construction de l'espace de
  travail). Couverture : `make test-cov`, seuil 80 %.
- Front : `make web-ci` = lint + typecheck + test + build.
- Charts : `make charts-lint` (helm lint + kubeconform). `make charts-test` exige le greffon
  helm-unittest **1.1.2** et Helm ≥ 3.18 ; sans eux il **refuse de jouer** plutôt que de rendre un
  verdict faux, et `tests/charts/` rend le chart et l'éprouve en Python.
- Générés à committer : `make contracts` (types depuis les schémas), `make docs-cli`
  (`docs/cli.md` depuis la CLI). Un test refuse la divergence.
- Au-delà de la CI : `make demo` (bout en bout en mémoire), `make dev-up` / `make dev-down`,
  `make e2e`, `make test-cluster` (vrai cluster), `make test-live` (vrais services),
  `make conformance`, `make evals`, `make replay-record`.

## Conventions
- Python 3.12, typage strict, pydantic v2, async sur toute I/O, structlog. TypeScript strict,
  ESLint, Prettier.
- Commits conventionnels : `feat(orchestrator): …`, `fix(runner): …`, `contract(schemas): …`.
  Une story = une PR = un squash.
- Fakes obligatoires : chaque intégration a son `Fake*` scriptable ; `CHOREGOS_FAKES=1` bascule
  toute la plateforme (ADR 0005).
- Idempotence : toute activité Temporal et tout pas de provisioning est rejouable sans double effet
  (ADR 0008). Pas de PR sur `workflows/` sans historique rejoué (`tests/replay/`).
- Sécurité : aucun secret en dur ; jamais de token large dans un runner ; toute écriture tracker via
  l'API interne.
- Journaux : JSON, clés `project`, `work_item`, `run_id`, `stage` quand elles existent.

## Definition of done (bloquante)
`make ci` vert, docs à jour, critères d'acceptation cochés dans la PR, pas de `TODO` sans issue
liée, `docs/plan/STATUS.md` mis à jour **en disant ce que la PR prouve et ce qu'elle ne prouve pas**.

**Rien n'est déclaré ✅ sans un test qui échoue en son absence.** Une fonctionnalité dont la garde
peut sauter sans faire rougir la suite n'est pas livrée, elle est espérée.

## Hors périmètre
Un problème découvert hors de ta story : n'y touche pas, ouvre une issue `finding` avec
`origin: <story>`, la preuve et une proposition. Besoin d'un contrat : issue `contract-change`.

## Mémoire
Lis `docs/plan/` et `docs/adr/` avant de décider ; ajoute un ADR (`docs/adr/NNNN-*.md`) pour toute
décision structurante. Quand une page et le code se contredisent, **le code gagne et la page est un
bogue**.
