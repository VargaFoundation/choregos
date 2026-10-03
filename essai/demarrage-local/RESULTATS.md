# Essai, élément 9 — démarrage local : mesure de référence du 2026-10-03

**Question** : la pile locale tient-elle « ≤ 10 minutes et ≤ 6 Go » (élément 9 de l'essai) ?

**Réponse provisoire : oui pour la mémoire, à risque pour le temps sur une connexion lente.** Mesure de
référence sur les briques existantes ; la pile intégrée de l'essai sera mesurée quand les éléments 2 à
6 tourneront.

## Mesures (images en cache, machine de développement)

| Pile | Démarrage (`up --wait`) | Mémoire au repos |
|---|---|---|
| Infrastructure de développement de Choregos (`dev/compose.yaml` : PostgreSQL + pgvector, Temporal et son interface, LiteLLM, Keycloak, MinIO, Ecphoria) | 8 s | 1,81 Gio (Keycloak 0,93 ; LiteLLM 0,53 ; PostgreSQL 0,15 ; Temporal 0,09) |
| Lakehouse de l'essai (Lakekeeper, sa base, MinIO ; élément 7) | quelques secondes | 0,34 Gio |
| API et orchestrateur (processus Python) | — | non mesurés ici ; de l'ordre de quelques centaines de Mio |

| Image | Taille |
|---|---|
| `ghcr.io/berriai/litellm:main-stable` | 1 174 Mo |
| `quay.io/keycloak/keycloak:26.0` | 442 Mo |
| `pgvector/pgvector:pg16` | 438 Mo |
| `temporalio/auto-setup:1.26.2` | 426 Mo |
| les cinq autres (MinIO, Lakekeeper, PostgreSQL, Temporal UI, Ecphoria) | 834 Mo |
| **Total à télécharger** | **3,3 Go** |

## Ce qu'on en tire

- **La mémoire tient** : environ 2,5 à 3 Gio pour tout, sous l'objectif de 6 Go. Le profil `core` en
  mode d'authentification local (ADR 0011 du cahier) se passe de Keycloak (−0,9 Gio) et de LiteLLM
  (−0,5 Gio).
- **Le temps dépend du téléchargement** : 3,3 Go, c'est environ 4 à 5 minutes à 100 Mbit/s, et le
  double à 50 Mbit/s. L'image LiteLLM en fait plus du tiers. Parades : profil `core` sans passerelle,
  images épinglées et allégées, et un essai mesuré sur une connexion lente avant le J0.
- **L'image MinIO n'est plus téléchargeable sur Docker Hub** (voir l'élément 7) : un poste neuf ne
  démarre ni cette pile ni `make dev-up` (issue `finding` ouverte).
- **Sous WSL2, des plages de ports sont réservées par Windows** : un port libre pour `ss` peut être
  refusé par Docker (« address already in use »). Les ports de `dev/compose.yaml` sont déjà
  surchargeables ; il faut le dire dans le guide de démarrage.
