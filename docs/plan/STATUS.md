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
| S2-12 | S2 | 🟡 | — | manifests de sandbox écrits (netpol, gVisor, quotas) ; test d'egress bloqué à faire sur kind |
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
| S4-05 | S4 | 🟡 | — | format Anthropic géré par backend ; test d'écho `/v1/messages` à ajouter |
| S4-06 | S4 | ✅ | — | profils plateforme/projet, validation, matrice opposable |
| S5-01 | S5 | ✅ | — | socle Next.js 15 / React 19 / TS strict / Tailwind + composants transverses |
| S5-02 | S5 | ✅ | — | projets et wizard de création avec validation par étape |
| S5-03 | S5 | ✅ | — | board : colonnes = états du DSL, décisions en ligne |
| S5-04 | S5 | ✅ | — | ticket (coût par étape, timeline) et run (journal virtualisé, diff, preuves) |
| S5-05 | S5 | 🟡 | — | éditeur YAML + validation par l'API + graphe par couloirs ; Monaco et React Flow non intégrés |
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
| S7-04 | S7 | 🟡 | — | CNPG ×3 avec Barman et sauvegarde planifiée ; restauration testée à faire en staging |
| S7-05 | S7 | 🟡 | — | Temporal déclaré dans les dépendances ; valeurs HA à régler au déploiement |
| S7-06 | S7 | 🟡 | — | Ecphoria et egress décrits ; chart Ecphoria attendu du flux S11 |
| S7-07 | S7 | ✅ | — | ServiceMonitor, 5 alertes, 6 dashboards Grafana livrés |
| S7-08 | S7 | 🟡 | — | overlays dev/staging/prod et fenêtres de synchronisation ; canary de la plateforme à câbler |
| S7-09 | S7 | ✅ | — | Kyverno (signatures, digests, non-root, labels, quotas, RuntimeClass) + ApplicationSet |
| S7-10 | S7 | 🟡 | — | 8 runbooks écrits ; exécution en staging et tests de chaos à faire |
| S8-01 | S8 | ✅ | — | template `github-tekton-argo-k8s` : manifeste, Tekton, Argo, scaffolding Jinja |
| S8-02 | S8 | ✅ | — | activités GitHub du provisioning, idempotentes |
| S8-03 | S8 | ✅ | — | rendu des manifests GitOps du projet (namespaces, quotas, netpol, RBAC, Argo) |
| S8-04 | S8 | ✅ | — | mémoire, gateway et notification dans les étapes de provisioning |
| S8-05 | S8 | ✅ | — | pipeline CI Tekton du template + Triggers + CloudEvents |
| S8-06 | S8 | 🟡 | — | tests de template sur kind à écrire (nightly) |
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
| S11-* | S11 | ⬜ | — | dépôt `VargaFoundation/ecphoria` : hors de ce monorepo (E-01 → E-14) |
| S12-01 | S12 | ✅ | — | 6 tickets de référence, dépôts jouets Python et Node, assertions vérifiées pour de vrai |
| S12-02 | S12 | ✅ | — | `EvalMatrix` : cellules backend × modèle × mémoire, publication opposable |
| S12-03 | S12 | ✅ | — | évals de playbooks : une dégradation de prompt fait échouer la CI |
| S12-04 | S12 | ✅ | — | 23 scénarios e2e M1–M5, sans cluster ; variantes kind en nocturne |
| S12-05 | S12 | 🟡 | — | reprise sans double coût prouvée ; chaos (kill worker, nœud spot) à jouer sur kind |
| S13-01 | S13 | ✅ | — | backend claude-code (hook de secours, modèles Claude uniquement) — conformité 7/7 |
| S13-02 | S13 | ✅ | — | codex, gemini-cli, goose, opencode, copilot-cli + versions.lock |
| S13-03 | S13 | ✅ | — | `cross_backend` appliquée au choix du relecteur, mesure du gain exposée (`metrics/cross-backend`) |
| S13-04 | S13 | ⬜ | — | tracker Jira/GitLab : webhooks acceptés, adaptateur non écrit |
| S13-05 | S13 | ⬜ | — | exécuteur ACA et template azure-devops-aca |
| S13-06 | S13 | ✅ | — | add-ons GitHub optionnels, désactivés par défaut |

**Total** : 89 livrées, 10 partielles, 3 non commencées.

## Ce qui tient debout aujourd'hui

- `make demo` : un ticket traverse la plateforme jusqu'à la production, sans cluster.
- `make ci` : lint, typage strict, 229 tests, contrats vérifiés, charts rendus.
- Les jalons M1 à M5 ont chacun leurs scénarios e2e, qui passent.
- Les 7 backends ACP passent les 7 contrôles de conformité.

## Ce qui manque pour dire « en production »

1. Un vrai cluster : les scénarios sur kind (provisioning réel, Tekton, Argo) restent à jouer.
2. Ecphoria (flux S11) vit dans son propre dépôt ; le repli pgvector couvre l'intervalle.
3. Les runbooks doivent être exécutés une fois en staging — un runbook non joué est une hypothèse.
4. Jira/GitLab et l'exécuteur ACA (S13-04, S13-05) ne sont pas écrits : les webhooks
   correspondants répondent honnêtement « adaptateur non activé ».
