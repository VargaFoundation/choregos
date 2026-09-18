# BLOCKERS

Ce que la plateforme **ne peut pas** faire ici, avec le contournement retenu. Un blocage
non écrit devient une surprise ; celui-ci est écrit.

| Flux | Story | Cause | Contournement retenu | Date |
|:--|:--|:--|:--|:--|
| S7, S8, S12 | S2-12, S8-06, S12-05 | Aucun cluster Kubernetes disponible dans cet environnement : les scénarios qui exigent kind (provisioning réel, Tekton, Argo, chaos, egress bloqué) ne peuvent pas être **exécutés** ici. | Les manifests, les exécuteurs et les tests sont écrits et rendus ; ils tournent en nocturne (`nightly.yml`, job `e2e` avec `helm/kind-action`). Les 23 scénarios M1–M5 ont une variante sans cluster, qui passe. | 2026-09-19 |
| S11 | S11-01 → S11-14 | Le flux Ecphoria vit dans `VargaFoundation/ecphoria`, un autre dépôt : E-01 à E-14 n'appartiennent pas à ce monorepo. | L'intégration côté Choregos est complète (`MemoryAdapter` Ecphoria, context pack, ingestion, file `pending`) et le repli `pgvector` implémente la même interface : la plateforme fonctionne sans Ecphoria. | 2026-09-19 |
| S13 | S13-04, S13-05 | Jira/GitLab et Azure Container Apps demandent des comptes et des environnements qui n'existent pas ici ; écrire un adaptateur non testable contre le vrai service serait du code non vérifié. | Les webhooks Jira et GitLab sont acceptés, signés et dédupliqués, et répondent `events: 0` avec un message explicite. L'`Executor` reste une interface à trois implémentations réelles (Tekton, Job K8s, Docker). | 2026-09-19 |
| S5 | S5-05 | Monaco et React Flow ajoutent ~2 Mo au front pour une valeur d'édition, pas de validation. | L'éditeur utilise un `textarea` et **la validation de l'API** (le même code que l'orchestrateur), avec les erreurs localisées ligne/colonne ; le graphe est rendu par couloirs. Le remplacement par Monaco + React Flow est une story front isolée. | 2026-09-19 |
| S4 | S4-05 | La compatibilité `/v1/messages` de LiteLLM se vérifie contre un vrai proxy, pas contre un modèle. | Le backend `claude-code` reçoit `ANTHROPIC_BASE_URL` et le format d'API est choisi par backend ; le test d'écho des en-têtes `anthropic-beta` est à ajouter à la conformité quand un LiteLLM est joignable en CI. | 2026-09-19 |
