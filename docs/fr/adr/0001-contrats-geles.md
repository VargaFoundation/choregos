# ADR-0001 — Les contrats sont gelés à M0 et versionnés

- **État** : acceptée (semaine 1)
- **Concerne** : tous les flux

## Contexte

Treize flux de travail avancent en parallèle. S'ils négocient leurs interfaces au fil de
l'eau, chaque intégration devient une renégociation : c'est le mode d'échec classique d'un
projet découpé en équipes.

## Décision

`packages/contracts` est la **source de vérité** des interfaces : JSON Schemas 2020-12,
OpenAPI 3.1, types Python et TypeScript générés. Les contrats sont gelés à la fin de la
semaine 1. Toute modification passe par une PR taguée `contract-change`, relue par le flux
intégrateur, et régénère les types.

Trois mécanismes rendent le gel effectif, plutôt que déclaratif :

1. les exemples (`schemas/examples/`) sont validés en CI **contre le schéma et contre le
   modèle Python** — une dérive entre les deux casse la CI ;
2. `make contracts-check` échoue si les types générés ne sont pas à jour ;
3. un test vérifie que l'API implémente exactement les chemins et les `operationId` du
   contrat — ni plus, ni moins.

## Conséquences

- Un flux bloqué par un contrat manquant ouvre une issue `contract-change` et continue
  avec un contournement local marqué `TODO(contract)`.
- Le front ne redéfinit jamais un type de l'API : il importe les types générés.
- Le coût du gel est une friction volontaire sur les changements d'interface.

## Alternatives écartées

- **Types partagés par un paquet Python seul** : le front aurait redéfini les siens.
- **Génération depuis FastAPI** : l'API serait devenue la spécification, et le front
  aurait attendu l'API pour démarrer.
