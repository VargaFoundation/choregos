# ADR-0005 — Tout connecteur a une interface et un fake

- **État** : acceptée
- **Concerne** : tous les flux

## Contexte

Treize flux en parallèle, et des connecteurs (GitHub, Tekton, Argo, LiteLLM, Ecphoria) qui
n'existent pas encore le premier jour. Attendre les connecteurs, c'est sérialiser le projet.

## Décision

Chaque intégration est un `Protocol` dans `packages/adapters/base.py`, avec **deux**
implémentations : la vraie, et un `Fake*` en mémoire **scriptable**. `CHOREGOS_FAKES=1`
bascule toute la plateforme sur les fakes.

Les fakes ne sont pas des bouchons vides : le tracker garde un commentaire de statut unique,
le gateway coupe au plafond, l'exécuteur est idempotent par `run_id`, la mémoire supersède
par sujet et rend un pack vide en cas de panne, le CD sait casser une analyse canary.

## Conséquences

- Le front, l'orchestrateur et le runner se développent et se testent sans aucun service.
- `make demo` joue la chaîne complète sur un poste, sans cluster.
- Les fakes doivent rester fidèles : quand un vrai adaptateur change de comportement, le
  fake suit — sinon les tests mentent.

## Alternatives écartées

- **Enregistrer des cassettes HTTP seulement** : bien pour un adaptateur, insuffisant pour
  écrire des scénarios (un canary cassé ne se rejoue pas depuis une cassette).
