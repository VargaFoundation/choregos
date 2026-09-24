# Contribuer à Choregos

## En cinq minutes

```bash
git clone https://github.com/VargaFoundation/choregos && cd choregos
make setup          # uv sync + pnpm install
make demo           # la chaîne complète, sans cluster : commence par là
make ci             # ce qui bloque une PR
```

`make demo` fait passer un ticket d'une issue à la production, avec des connecteurs
simulés. Si vous ne devez lire qu'une chose pour comprendre la plateforme, lisez sa sortie.

## Organisation du dépôt

Le plan d'exécution complet est dans [`plan/00-index.md`](plan/00-index.md). Il décrit
l'architecture, les contrats, le backlog et les conventions. Les décisions structurantes
sont dans [`adr/`](adr/). Ce qui se passe quand ça casse est dans [`runbooks/`](runbooks/).

## Le contrat avant le code

`packages/contracts` est la source de vérité des interfaces. On ne le modifie pas au fil de
l'eau : une PR taguée `contract-change`, relue par le flux intégrateur, et
`make contracts` pour régénérer les types (à committer).

Si un contrat vous manque : ouvrez l'issue, continuez avec un contournement local marqué
`TODO(contract)`, et ne bloquez pas.

## Une story, une PR

- Une branche `stream/<Sx>/<story>`, une PR, un squash.
- La description reprend le gabarit : story, changement, critères d'acceptation cochés,
  preuves (commandes et résultats), contrats, findings déposés.
- CI verte obligatoire. Pas de force-push sur `main`.

## Ce que la CI vérifie

| Commande | Ce qu'elle garantit |
| :-- | :-- |
| `make lint` | ruff, format compris |
| `make typecheck` | `mypy --strict` sur tout le Python |
| `make test` | tests unitaires et d'intégration (fakes) |
| `make contracts-check` | les types générés sont à jour |
| `make charts-lint` | les charts rendent et valident |
| `make web-ci` | lint, typage, tests et build du front |

Le nocturne ajoute : e2e sur kind, conformité de tous les backends ACP, évals de playbooks,
replay des historiques Temporal, scan des dépendances.

## Écrire du code ici

- **Python 3.12**, typage strict, `pydantic` v2, asynchrone côté I/O, `structlog` en JSON.
- **Le cœur ne fait pas d'I/O** : `packages/core` est pur et testable sans service.
- **Tout ce qui parle au monde** est un adaptateur, avec son `Protocol` et son fake.
- **Idempotence** : toute activité Temporal et tout step de provisioning se rejouent sans
  effet double (ADR-0008). Un test le prouve.
- **Une garantie est un mécanisme** (ADR-0010) : si vous vous surprenez à écrire une
  consigne dans un prompt pour garantir une propriété, cherchez le mécanisme.
- **Secrets** : jamais en dur, jamais dans un workspace d'agent, jamais dans Git.

## Hors périmètre

Vous découvrez un problème qui n'est pas dans votre story ? N'y touchez pas : ouvrez une
issue `finding` avec l'origine, la preuve et une proposition. C'est exactement ce qu'on
demande aux agents.

## Le dépôt est son propre client

Dès M1, `choregos` est un projet Choregos : les stories passent par la plateforme. Quand
vous travaillez ici, vous travaillez aussi sur l'outil qui vous relit.
