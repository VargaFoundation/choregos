# ADR-0009 — Le cluster ne se modifie que par Git

- **État** : acceptée
- **Concerne** : S7, S8, S9

## Contexte

La plateforme crée des namespaces, des quotas, des politiques réseau et des applications
pour chaque projet provisionné. Lui donner les droits de le faire directement, c'est donner
`cluster-admin` à un service qui exécute du code d'agents.

## Décision

Aucun composant de Choregos n'applique de manifeste. Le provisioning **écrit dans Git**
(`choregos-infra/projects/<slug>/`) ; un `ApplicationSet` Argo CD synchronise. La promotion
d'une release est une **PR** sur le dépôt GitOps du projet, jamais un `kubectl set image`.

L'API n'a qu'un droit de lecture sur les `PipelineRun` ; l'orchestrateur n'a de droits que
dans les namespaces `proj-*-runners`, par des `Role` générés projet par projet.

## Conséquences

- Tout changement de cluster est auditable, relisible et réversible (`prune`).
- Supprimer un projet = supprimer un répertoire.
- Le provisioning est plus lent qu'un appel direct : c'est le prix de la traçabilité.

## Alternatives écartées

- **API Kubernetes directe depuis l'orchestrateur** : rapide, mais donne à la plateforme
  des droits qu'aucune revue ne peut plus encadrer.
