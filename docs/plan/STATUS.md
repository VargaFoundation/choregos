# STATUS

État des stories du backlog (`08-backlog.yaml`), tenu par le flux intégrateur.

Légende : ✅ livrée et testée · 🟡 livrée partiellement (le reste est dit) · ⬜ non commencée.

| Story | Flux | État | PR | Notes |
|:--|:--|:--|:--|:--|
| S0-01 | S0 | ✅ | — | monorepo uv + pnpm, Makefile, ruff/mypy strict, AGENTS.md, CODEOWNERS, template de PR |
| S0-02 | S0 | ✅ | — | 11 JSON Schemas 2020-12 + 10 exemples validés contre schéma **et** modèle Python |
| S0-03 | S0 | ✅ | — | OpenAPI 3.1 (68 chemins, 81 opérations) + génération TS déterministe hors ligne |
| S0-04 | S0 | ✅ | — | DSL : parseur positionné, 14 règles, 3 templates, `select_transition` déterministe |
| S0-05 | S0 | ✅ | — | policy engine (budgets, approbations, tentatives, périmètre) + 3 presets |
| S0-06 | S0 | ✅ | — | 9 Protocol + fakes scriptables ; `CHOREGOS_FAKES=1` |
| S0-07 | S0 | ✅ | — | kind, compose, Tiltfile, seed ; `make dev-up` / `dev-down` / `dev-seed` |
| S0-08 | S0 | ✅ | — | ci.yml ciblé par chemins, nightly.yml, release.yml (cosign, SBOM, chart OCI) |
| S0-09 | S0 | ✅ | — | 10 ADR, 9 runbooks, guide contributeur, dev.md, securite.md, SECURITY.md |
| S1-01 | S1 | ✅ | — | worker multi-queues, répartition des activités, OTel via structlog |
| S1-02 | S1 | ✅ | — | `WorkflowInterpreter` : boucle d'états, tentatives bornées, `continue_as_new` |
| S1-03 | S1 | ✅ | — | activités de stage idempotentes (`run_id` déterministe), annulation, heartbeat |
| S1-04 | S1 | ✅ | — | attente humaine : demande, SLA, rappel, escalade, décision par signal |
| S1-05 | S1 | ✅ | — | 11 gates, synchrones et asynchrones, avec test vert et test rouge chacune |
| S1-06 | S1 | ✅ | — | miroir tracker : état, commentaire de suivi unique, champs du board |
| S1-07 | S1 | ✅ | — | findings (signal + persistance depuis le résultat) et scope change auto/humain |
| S1-08 | S1 | ✅ | — | migration de workflow, reprise, replay des historiques en CI |
| S1-09 | S1 | ✅ | — | `ProjectProvisioning` : étapes du template, reprise, remédiation |
| S1-10 | S1 | ✅ | — | démarrage depuis InboundEvent, ID déterministe : un seul workflow par ticket |
| S2-01 | S2 | ✅ | — | CLI du runner, StageInput, workspace init+fetch, codes de sortie 0/10/20/30/40 |
| S2-02 | S2 | ✅ | — | client ACP complet + agent factice scriptable (17 tests de protocole) |
| S2-03 | S2 | ✅ | — | backend OpenHands (skills, MCP, AGENTS.md) — conformité 7/7 |
| S2-04 | S2 | ✅ | — | guardrails : périmètre, commandes, motifs dangereux, réseau, fichiers sensibles |
| S2-05 | S2 | ✅ | — | boucle DoD bornée, vérification de diff avec revert, réparation du résultat |
| S2-06 | S2 | ✅ | — | sidecar MCP `choregos-tools` : 10 outils, plafond de findings, scope change |
| S2-07 | S2 | ✅ | — | commit conventionnel, push, transcript JSONL, dépôt du résultat |
| S2-08 | S2 | ✅ | — | image runner : agents épinglés, scanners, shims de refus, non-root |
| S2-09 | S2 | ✅ | — | exécuteur Tekton : PipelineRun, Secret par run, result-url, annulation |
| S2-10 | S2 | ✅ | — | exécuteurs Job Kubernetes et Docker local, même contrat |
| S2-11 | S2 | ✅ | — | suite de conformité : 7 contrôles × 7 backends + rapport de désactivation |
| S2-12 | S2 | ✅ | — | egress bloqué **vérifié** sur kind + Calico (7 tests) : internet fermé, DNS ouvert, API interne joignable, port non listé fermé, pod privilégié refusé |
| S3-01 | S3 | ✅ | — | App GitHub : JWT, jetons d'installation scopés, cache, backoff |
| S3-02 | S3 | ✅ | — | tracker Issues + Projects v2 (GraphQL), option Status créée si absente |
| S3-03 | S3 | ✅ | — | webhooks → InboundEvent, HMAC, dédup, commandes `/choregos …` |
| S3-04 | S3 | ✅ | — | SCM : branche, PR, checks, reviews, merge queue, compare |
| S3-05 | S3 | ✅ | — | check-runs `choregos/scope` et `choregos/evidence` alimentés par les gates |
| S3-06 | S3 | ✅ | — | `TrackerReconciliation` : rattrapage toutes les 60 s, démarrage idempotent, runbook |
| S3-07 | S3 | ✅ | — | Slack : blocs, boutons Approuver/Renvoyer |
| S4-01 | S4 | ✅ | — | config LiteLLM (dev + plateforme), Postgres et Redis dans les charts |
| S4-02 | S4 | ✅ | — | GatewayAdapter : mint (plafond dur), spend, revoke, list_models |
| S4-03 | S4 | ✅ | — | `cost_ledger`, fx, agrégats par jour/étape/modèle/backend/taille |
| S4-04 | S4 | ✅ | — | estimation médiane/p80 sur 90 jours, alerte de dépassement |
| S4-05 | S4 | ✅ | — | `/v1/messages` vérifié contre un vrai LiteLLM ; l'environnement remis à `claude-code` appelle pour de vrai (8 tests live) |
| S4-06 | S4 | ✅ | — | profils plateforme/projet, validation, matrice opposable |
| S5-01 | S5 | ✅ | — | socle Next.js 15 / React 19 / TS strict / Tailwind + composants transverses |
| S5-02 | S5 | ✅ | — | projets et wizard de création avec validation par étape |
| S5-03 | S5 | ✅ | — | board : colonnes = états du DSL, décisions en ligne |
| S5-04 | S5 | ✅ | — | ticket (coût par étape, timeline) et run (journal virtualisé, diff, preuves) |
| S5-05 | S5 | ✅ | — | Monaco (erreurs de l'API dans la marge) + React Flow par couloirs, chargés à la demande |
| S5-06 | S5 | ✅ | — | trains : lot, départ, gel avec motif obligatoire, approbation confirmée |
| S5-07 | S5 | ✅ | — | findings et mémoire (recherche, file `pending`) |
| S5-08 | S5 | ✅ | — | paramètres (connecteurs, politique, matrice) et administration (audit) |
| S5-09 | S5 | ✅ | — | vue d'ensemble : coûts, qualité, quatre mesures DORA, export CSV des coûts |
| S5-10 | S5 | ✅ | — | 7 parcours Playwright en mode démo ; accessibilité de base |
| S6-01 | S6 | ✅ | — | FastAPI, RFC 9457, structlog, SQLAlchemy async, Alembic, RLS PostgreSQL |
| S6-02 | S6 | ✅ | — | OIDC, sessions signées, jetons d'API, RBAC 5 rôles, audit systématique |
| S6-03 | S6 | ✅ | — | projets, connecteurs (+ test), workflow et politique (422 localisé), modèles |
| S6-04 | S6 | ✅ | — | work items, runs, décisions, actions, SSE reprenable |
| S6-05 | S6 | ✅ | — | webhooks GitHub/Tekton/Argo/Alertmanager (+ Jira/GitLab annoncés inactifs) |
| S6-06 | S6 | ✅ | — | API interne : input, events idempotents, result idempotent, findings, scope, question |
| S6-07 | S6 | ✅ | — | trains, releases, findings, mémoire, coûts, templates, admin, audit |
| S6-08 | S6 | ✅ | — | CLI `choregos` complète, `runs tail` en SSE |
| S7-01 | S7 | ✅ | — | umbrella + 4 sous-charts, helm unittest, rendu des 3 environnements |
| S7-02 | S7 | ✅ | — | app-of-apps par sync-waves : opérateurs, CNPG, Tekton, gVisor, monitoring |
| S7-03 | S7 | ✅ | — | namespaces, NetworkPolicies, RBAC limité aux `proj-*-runners`, PodSecurity |
| S7-04 | S7 | ✅ | — | restauration **jouée** sur cluster : sauvegarde Barman → cluster détruit → restauré → données relues ; `serverName` manquant corrigé dans le runbook |
| S7-05 | S7 | 🟡 | — | API + workers + Temporal tournent sur kind depuis l'image du dépôt (4 tests) ; valeurs HA (3 nœuds, persistance) à régler au déploiement réel |
| S7-06 | S7 | ✅ | — | Application Argo `ecphoria` (vague 2) sur le chart du dépôt amont, valeurs vérifiées par `helm template` et acceptées par un serveur d'API ; embeddings routés par la passerelle (E-08) |
| S7-07 | S7 | ✅ | — | ServiceMonitor, 5 alertes, 6 dashboards Grafana livrés |
| S7-08 | S7 | ✅ | — | l'API se déploie en `Rollout` quand `global.canary.enabled` ; vérifié sur cluster : la nouvelle version est retenue à 10 %, l'abandon restaure la stable |
| S7-09 | S7 | ✅ | — | Kyverno (signatures, digests, non-root, labels, quotas, RuntimeClass) + ApplicationSet |
| S7-10 | S7 | 🟡 | — | 9 runbooks ; celui de restauration Postgres **exécuté** (et corrigé) sur cluster ; les autres restent à jouer en staging |
| S8-01 | S8 | ✅ | — | template `github-tekton-argo-k8s` : manifeste, Tekton, Argo, scaffolding Jinja |
| S8-02 | S8 | ✅ | — | activités GitHub du provisioning, idempotentes |
| S8-03 | S8 | ✅ | — | rendu des manifests GitOps du projet (namespaces, quotas, netpol, RBAC, Argo) |
| S8-04 | S8 | ✅ | — | mémoire, gateway et notification dans les étapes de provisioning |
| S8-05 | S8 | ✅ | — | pipeline CI Tekton du template + Triggers + CloudEvents |
| S8-06 | S8 | 🟡 | — | conformité hors cluster + manifests acceptés par un vrai serveur d'API (dry-run serveur) ; provisioning complet sur kind en nocturne |
| S9-01 | S9 | ✅ | — | `ReleaseTrain` complet : fenêtres, cron, lots, express, gel, approbation |
| S9-02 | S9 | ✅ | — | CdAdapter Argo : promotion par PR GitOps, santé, rollout, abandon, fenêtres |
| S9-03 | S9 | ✅ | — | AnalysisTemplate SLO, soak, smoke ; canary cassé → rollback prouvé par test |
| S9-04 | S9 | ✅ | — | approbation (API + Slack + front), notes de release, incident enregistré |
| S9-05 | S9 | ✅ | — | `apply` Atlantis déclenché pendant le départ, après approbation ; échec ⇒ rollback |
| S9-06 | S9 | ✅ | — | gate `flag_present` avec message explicite |
| S10-01 | S10 | ✅ | — | MemoryAdapter Ecphoria (circuit-breaker) et repli pgvector, même interface |
| S10-02 | S10 | ✅ | — | `FindingsTriage` : dédup, ticket lié, commentaire d'origine, notification, mémoire |
| S10-03 | S10 | ✅ | — | `MemoryIngestion` : tickets, runs, déploiements, findings, upsert idempotent |
| S10-04 | S10 | ✅ | — | context pack réel par rôle, budget de tokens, archivé avec le run |
| S10-05 | S10 | ✅ | — | écriture gouvernée : `write_fact`, `propose_fact`, file `pending`, auto-accept |
| S10-06 | S10 | ✅ | — | faux positifs alertés ; rapport A/B hebdomadaire (premier passage, coût/ticket) posté et exposé |
| S11-* | S11 | ✅ | — | E-01 à E-14 livrés et poussés sur `main` amont : faits typés + gouvernance par tenant, deux éditions (`ecphoria:memory` / `:full`), sauvegardes planifiées à manifeste vérifié, outillage de release, revue sécurité, doc d'intégration, et banc au profil Choregos (`docs/benchmarks-choregos.md`). Le banc a trouvé deux défauts réels côté cluster, corrigés : un client ordinaire derrière un Service perdait (N-1)/N de ses écritures, et toute recherche était traitée comme une écriture |
| S12-01 | S12 | ✅ | — | 6 tickets de référence, dépôts jouets Python et Node, assertions vérifiées pour de vrai |
| S12-02 | S12 | ✅ | — | `EvalMatrix` : cellules backend × modèle × mémoire, publication opposable |
| S12-03 | S12 | ✅ | — | évals de playbooks : une dégradation de prompt fait échouer la CI |
| S12-04 | S12 | ✅ | — | 23 scénarios e2e M1–M5, sans cluster ; variantes kind en nocturne |
| S12-05 | S12 | ✅ | — | reprise sans double coût prouvée ; worker tué et remplacé sur cluster ; **perte d'un nœud jouée sur cluster** (`tests/cluster/test_node_loss.py`) : 359 s d'immobilité avec les défauts Kubernetes, 74 s avec les tolérances désormais dans le chart. Reste non couvert : le préavis d'éviction spot, que la plateforme n'écoute pas (`docs/runbooks/perte-de-noeud.md`) |
| S13-01 | S13 | ✅ | — | backend claude-code (hook de secours, modèles Claude uniquement) — conformité 7/7 |
| S13-02 | S13 | ✅ | — | codex, gemini-cli, goose, opencode, copilot-cli + versions.lock |
| S13-03 | S13 | ✅ | — | `cross_backend` appliquée au choix du relecteur, mesure du gain exposée (`metrics/cross-backend`) |
| S13-04 | S13 | 🟡 | — | GitLab **vérifié contre gitlab.com** (cycle complet sur un projet bac à sable) ; Jira écrit et testé contre le protocole, pas encore contre une instance |
| S13-05 | S13 | 🟡 | — | exécuteur ACA **vérifié contre un vrai abonnement Azure** (6 tests live : cycle complet, `start` rejoué sans double exécution, jeton absent d'ARM, annulation, 404, logs) — trois défauts trouvés et corrigés au passage ; template `github-aca` livré. `azure-devops-aca` complet attend une organisation Azure DevOps (Boards + Pipelines), qu'un abonnement ne fournit pas |
| S13-06 | S13 | ✅ | — | add-ons GitHub optionnels, désactivés par défaut |

**Total** : 96 livrées, 6 partielles, 0 non commencée.

## Ce qui tient debout aujourd'hui

- `make demo` : un ticket traverse la plateforme jusqu'à la production, sans cluster.
- `make ci` : lint, typage strict, 390 tests, contrats vérifiés, charts rendus.
- Couverture 84 % sur `packages/core`, `packages/runner`, `apps/orchestrator` (seuil : 80 %).
- 24 scénarios e2e M1–M5 au vert, sans cluster.
- **Sur un vrai cluster** (kind + Calico) : l'egress d'un runner est bloqué pour de bon, la
  sauvegarde Postgres se restaure avec ses données, les manifests générés sont acceptés par le
  serveur d'API, et l'API + les workers tournent depuis l'image du dépôt — worker tué compris.
- **Contre les vrais services** : GitLab (cycle complet sur gitlab.com), Ecphoria (context pack,
  file de validation, upsert idempotent), LiteLLM (clé de run, budget en plafond dur, coût
  mesuré à la passerelle, format Anthropic) et **Azure Container Apps** (un job créé, déclenché,
  observé jusqu'à son état terminal, annulé — sur un abonnement réel).
- Les 7 backends ACP passent les 7 contrôles de conformité ; les 2 templates passent la
  conformité de template.

## Ce qui manque pour dire « en production »

1. **Jira n'a jamais répondu** : le token fourni est refusé (`AUTHENTICATED_FAILED`) sur
   meltingcode.atlassian.net, y compris via `api.atlassian.com/ex/jira`. L'adaptateur reste
   vérifié contre le protocole seulement. Un token valide suffit à lever ce point.
2. **Le déploiement réel** : la pile de test sur kind est volontairement petite (un Temporal de
   développement, un Postgres simple). Les valeurs HA — Temporal à trois nœuds, CNPG à trois
   instances, Argo Rollouts — restent à régler sur un vrai environnement.
3. **Les runbooks restants** doivent être joués une fois en staging. Celui de restauration l'a
   été, et il était faux : c'est l'argument pour jouer les autres.
4. **Le chaos au-delà du nœud** : coupure réseau, disque plein. La perte d'un nœud est jouée
   (`tests/cluster/test_node_loss.py`) ; ce qui reste est le préavis d'éviction spot, que la
   plateforme subit au lieu de l'écouter.
5. **Ecphoria** : E-01 à E-14 sont livrés et poussés sur `main` amont. Ce qui en ressort et qui
   nous concerne : le banc au profil Choregos (20 000 faits, 200 000 événements, 50 lectures/s)
   mesure la recherche à ~250 ms p50 / ~540 ms p95 sur une station de travail, au-dessus de la
   cible de 300 ms p95 ; `retrieval_scan_cap` à 512 ramène p95 à 24 ms sans coût de rappel
   mesurable à cette taille de corpus. À décider au provisionnement, pas en production.
