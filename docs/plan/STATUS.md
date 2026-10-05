# STATUS

État des stories du backlog (`08-backlog.yaml`), tenu par le flux intégrateur.

Le dépôt vit sur `github.com/VargaFoundation/choregos` depuis le 2026-09-19. Le premier
passage de la CI ailleurs que sur la station de travail a trouvé six choses qu'aucune
exécution locale ne pouvait voir — une action GitHub référencée par un tag qui n'existe pas,
un glob mypy qui attrapait du TypeScript, `helm unittest` qui ne s'était jamais exécuté (et
dont les quatre tests étaient faux), l'image des workers qui partait du commit précédent,
l'image web qui ne se construisait pas, et la porte `ci-ok` qui ne regardait pas les images.
C'est la valeur d'un dépôt distant, mesurée en une heure.

Légende : ✅ livrée et testée · 🟡 livrée partiellement (le reste est dit) · ❌ annoncée et fausse · ⬜ non commencée.

**2026-09-24 — état des lieux.** Trois audits croisés avec le banc réel ont montré que plusieurs ✅
de ce tableau étaient faux ; ils sont corrigés ci-dessous, et le détail est dans
[STATE-OF-THE-PROJECT-2026-09-24.md](STATE-OF-THE-PROJECT-2026-09-24.md), avec le plan P0 → P3
qui en découle. Règle depuis ce jour : rien n'est ✅ sans un test qui échoue en son absence.

| Story | Flux | État | PR | Notes |
|:--|:--|:--|:--|:--|
| S0-01 | S0 | ✅ | — | monorepo uv + pnpm, Makefile, ruff/mypy strict, AGENTS.md, CODEOWNERS, template de PR |
| S0-02 | S0 | ✅ | — | 11 JSON Schemas 2020-12 + 10 exemples validés contre schéma **et** modèle Python |
| S0-03 | S0 | ✅ | — | OpenAPI 3.1 (68 chemins, 81 opérations) + génération TS déterministe hors ligne |
| S0-04 | S0 | ✅ | — | DSL : parseur positionné, 14 règles, 3 templates, `select_transition` déterministe |
| S0-05 | S0 | ✅ | — | policy engine (budgets, approbations, tentatives, périmètre) + 3 presets |
| S0-06 | S0 | ✅ | — | 9 Protocol + fakes scriptables ; `CHOREGOS_FAKES=1` |
| S0-07 | S0 | ✅ | — | kind, compose, Tiltfile, seed ; `make dev-up` / `dev-down` / `dev-seed` |
| S0-08 | S0 | 🟡 | — | ci.yml ciblé par chemins, nightly.yml, release.yml (cosign, chart OCI). **Pas de SBOM** malgré ce que disait cette ligne et `SECURITY.md` ; pas de Trivy sur les images de release ; scan nocturne non bloquant (état des lieux du 2026-09-24, P1-2) |
| S0-09 | S0 | 🟡 | — | 14 ADR, 11 runbooks, guide contributeur, dev.md, securite.md, SECURITY.md — en français ; l'anglais devient la référence le 2026-09-24 (P1-1). `SECURITY.md` promettait un SBOM et un blocage CRITICAL nocturne qui n'existent pas |
| S1-01 | S1 | ✅ | — | worker multi-queues, répartition des activités, OTel via structlog |
| S1-02 | S1 | ✅ | — | `WorkflowInterpreter` : boucle d'états, tentatives bornées, `continue_as_new` |
| S1-03 | S1 | ✅ | — | activités de stage idempotentes (`run_id` déterministe), annulation, heartbeat |
| S1-04 | S1 | ✅ | — | attente humaine : demande, SLA, rappel, escalade, décision par signal |
| S1-05 | S1 | ✅ | — | 11 gates, synchrones et asynchrones, avec test vert et test rouge chacune |
| S1-06 | S1 | ✅ | — | miroir tracker : état, commentaire de suivi unique, champs du board |
| S1-07 | S1 | ✅ | — | findings (signal + persistance depuis le résultat) et scope change auto/humain |
| S1-08 | S1 | 🟡 | — | migration de workflow, reprise. **Le replay des historiques ne tourne pas** : `tests/replay/histories/` est vide, le test paramétré skippe, et `make replay-record` n'existe pas (P0-2) |
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
| S3-03 | S3 | 🟡 | — | webhooks → InboundEvent, HMAC, dédup. Les commandes `/choregos …` sont analysées mais **l'orchestrateur ne les traduit en rien** (#138) ; le commentaire de demande humaine y invitait jusqu'au 2026-09-29 — il renvoie désormais vers l'interface |
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
| S5-01 | S5 | ✅ | — | socle Next.js / React 19 / TS strict / Tailwind + composants transverses. Interface **en anglais** depuis le 2026-09-25 (P1-1b) ; `next-intl` retiré le 2026-09-24 (déclaré, jamais branché — pas de demi-état) |
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
| S7-07 | S7 | 🟡 | — | `/metrics` sur l'API depuis le 2026-09-24 (soir) : quinze séries **calculées depuis la base** (runs, coûts, budgets, releases, délais, refus, mémoire, trains via Temporal) + `http_requests_total` ; un test interdit à un tableau de bord ou une alerte de citer une série absente ; deux panneaux sans mesure retirés (`webhook_to_signal`, `dod_iterations`). **Pas de traces** (P3-5) |
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
| S10-06 | S10 | 🟡 | — | faux positifs alertés ; rapport A/B hebdomadaire posté et exposé par l'API (`/orgs/{org}/memory/ab-report`) — **aucun écran ne l'affiche** (P0-5) |
| S11-* | S11 | ✅ | — | E-01 à E-14 livrés et poussés sur `main` amont : faits typés + gouvernance par tenant, deux éditions (`ecphoria:memory` / `:full`), sauvegardes planifiées à manifeste vérifié, outillage de release, revue sécurité, doc d'intégration, et banc au profil Choregos (`docs/benchmarks-choregos.md`). Le banc a trouvé deux défauts réels côté cluster, corrigés : un client ordinaire derrière un Service perdait (N-1)/N de ses écritures, et toute recherche était traitée comme une écriture |
| S12-01 | S12 | ✅ | — | 6 tickets de référence, dépôts jouets Python et Node, assertions vérifiées pour de vrai |
| S12-02 | S12 | ✅ | — | `EvalMatrix` : cellules backend × modèle × mémoire, publication opposable |
| S12-03 | S12 | ✅ | — | évals de playbooks : une dégradation de prompt fait échouer la CI |
| S12-04 | S12 | ✅ | — | 23 scénarios e2e M1–M5, sans cluster ; variantes kind en nocturne |
| S12-05 | S12 | ✅ | — | reprise sans double coût prouvée ; worker tué et remplacé sur cluster ; **perte d'un nœud jouée sur cluster** (`tests/cluster/test_node_loss.py`) : 359 s d'immobilité avec les défauts Kubernetes, 74 s avec les tolérances désormais dans le chart. Reste non couvert : le préavis d'éviction spot, que la plateforme n'écoute pas (`docs/runbooks/perte-de-noeud.md`) |
| S13-01 | S13 | ✅ | — | backend claude-code (hook de secours, modèles Claude uniquement) — conformité 7/7 |
| S13-02 | S13 | ✅ | — | codex, gemini-cli, goose, opencode, copilot-cli + versions.lock |
| S13-03 | S13 | 🟡 | — | `cross_backend` appliquée au choix du relecteur, mesure exposée par l'API (`metrics/cross-backend`) — **aucun écran ne l'affiche**, et le calcul est dupliqué entre l'API et l'orchestrateur (P0-5, P1-4) |
| S13-04 | S13 | ✅ | — | GitLab **vérifié contre gitlab.com** (cycle complet sur un projet bac à sable) et Jira **vérifié contre un vrai site** (`tests/live/test_jira_live.py`, projet `CHOTEST`) : la confrontation a trouvé qu'un Jira francophone appelle « In Progress » « En cours » — aucun ticket ne bougeait |
| S13-05 | S13 | 🟡 | — | exécuteur ACA **vérifié contre un vrai abonnement Azure** (6 tests live : cycle complet, `start` rejoué sans double exécution, jeton absent d'ARM, annulation, 404, logs) — trois défauts trouvés et corrigés au passage ; template `github-aca` livré. `azure-devops-aca` complet attend une organisation Azure DevOps (Boards + Pipelines), qu'un abonnement ne fournit pas |
| S13-06 | S13 | ✅ | — | add-ons GitHub optionnels, désactivés par défaut |
| S14-01 | S14 | ⬜ | — | entrées de catalogue pour une plateforme data (graal d'abord) — ADR 0028 |
| S14-02 | S14 | ⬜ | — | garantie `data_quality` sur un échantillon mesuré par la plateforme |
| S14-03 | S14 | ⬜ | — | gabarit `data-change` : PR → garanties → validation → application par l'API de la plateforme |
| S14-04 | S14 | ⬜ | — | rapport d'adoption par projet (coût par ticket, délai, acceptation, reprises) |
| S15-01 | S15 | ✅ | — | jetons d'API à portée (`*`, `mcp:read`, `mcp:write`), liés ou non à un projet — ADR 0030. Un jeton sans `*` reçoit 403 sur toute route REST (`/me`, `/me/tokens`, les projets) ; `*` ne se combine pas ; un projet ne borne qu'un jeton MCP et doit être lisible par l'humain ; le dernier client (`User-Agent`) est noté. `test_jetons_a_portee.py` (7 tests, rouge sans la garde de `deps.py`) ; `choregos tokens create --scope --project`. Ne prouve pas la porte MCP elle-même (S15-02) |
| S15-02 | S15 | ⬜ | — | serveur MCP pour les clients externes, `/mcp` et `/mcp/projects/{P}`, par jeton à portée |
| S15-03 | S15 | ⬜ | — | outils générés de l'ontologie à travers la porte, avec les droits de l'humain |
| S15-04 | S15 | ⬜ | — | décider une proposition d'action dans la console, ré-authentifié |
| S15-05 | S15 | ⬜ | — | page Integrations (neuf clients, jeton par client, indicateur « connecté », dépannage) |
| S15-06 | S15 | ⬜ | — | plugin Claude Code et skill Choregos |
| S15-07 | S15 | ⬜ | — | essai sur le locataire dev : Claude Code liste les outils et crée un ticket |
| S15-08 | S15 | ⬜ | — | serveur de ressources OAuth (RFC 9728), jetons émis par l'IdP |

**Total** : 92 livrées, 11 partielles, 11 non commencées.

**2026-10-05 — ADR 0029 : Choregos devient une plateforme d'agents gouvernés, à usage général ; la 0028 est
remplacée.** La revue de la console du dev a relevé cinq manques (un seul workflow par projet, aucune fonction
entreprise visible, agents et serveurs MCP invisibles, réglages typés développement, aucune connexion depuis Claude) ;
le product owner a choisi d'élargir le périmètre plutôt que de le resserrer. Six flux s'ouvrent, S15 à S20 : leurs
stories entrent au tableau au début de chaque lot. Ce que cette PR prouve : rien de fonctionnel ; elle consigne la
décision, ses invariants (aucun secret dans un pod, un outil découvert reste fermé, aucune décision par MCP) et ce
qui nous ferait changer d'avis. Ce qu'elle ne prouve pas : qu'un seul de ces flux marche — chacun le prouvera par ses
tests. Le flux S14 reste valable : le travail data entre toujours par le catalogue et les garanties.

**2026-10-02 — ADR 0028 : Choregos gouverne l'exécution du changement.** Le flux S14 ouvre le travail data par le
catalogue et les garanties, sans fusion avec graal. Ce que cette PR prouve : rien de fonctionnel ; elle consigne une
décision et ouvre quatre stories ⬜, chacune avec son critère vérifiable. Ce qu'elle ne prouve pas : qu'un agent ait
déjà piloté une plateforme data sous ces garanties — c'est l'objet de S14-03.

Depuis le 2026-09-23, le dépôt a reçu du matériel qui ne correspond à aucune story du backlog
initial — il est venu de l'usage : dépendances embarquées en option, banc mono-nœud (`demo/`),
catalogue d'outils, file d'admission des runs, capacités d'exécuteur, documentation anglaise.
Les ADR 0012 à 0014 en portent les décisions.

## Ce qui tient debout aujourd'hui

- `make demo` : un ticket traverse la plateforme jusqu'à la production, sans cluster.
- `make ci` : lint, typage strict, 465 tests, contrats vérifiés, charts rendus.
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
- **Une démonstration mono-nœud, avec de vrais agents** (2026-09-23, `demo/`) : trois tickets de
  code traversent le workflow jusqu'à `done` — un agent implémente, un **second** vérifie et
  ajoute des tests de cas limites, la garantie `evidence_present` porte sur des tests réellement
  exécutés, et les branches sont poussées. Deux tickets RH suivent le **même moteur** sans une
  ligne de code changée : faute de base de profils, les agents refusent d'inventer des candidats,
  le ticket monte en `needs_human` et leurs findings deviennent des tickets.
- Les dépendances (PostgreSQL, Temporal, LiteLLM, Ecphoria) s'embarquent ou se branchent en
  externe, à la manière des charts Bitnami : `global.<dépendance>.embedded`. Défaut inchangé
  (externe) ; `charts/choregos/values/local.yaml` monte un banc complet sans rien fournir.
- **Le moteur porte un métier sans dépôt ni tests** (2026-09-24, [ADR 0012](../adr/0012-le-moteur-n-est-pas-lie-au-logiciel.md)) :
  `repo` est facultatif — l'agent travaille alors dans un répertoire vide, suivi par un git
  local pour que son travail reste mesurable ; les preuves se nomment par le métier
  (`Evidence.facts` + garantie `evidence_facts`, qui refuse un zéro poli) ; et les rôles sont
  des noms de plein droit (`sourcing`, `qualification`) et non plus `custom`. Le projet RH de
  la démonstration n'a plus aucun dépôt.
- **Une pointe de tickets ne fait plus une pointe de pods** ([ADR 0013](../adr/0013-densite-des-taches-d-agent.md)) :
  au-delà de `runner.maxActive`, le Job est créé suspendu — aucun pod, aucune image tirée — et
  admis dans l'ordre d'arrivée. Un exécuteur déclare ses capacités (`queue`, `suspend`,
  `resume`, `snapshot`) et la conformité refuse qu'il en annonce une qu'il n'implémente pas :
  c'est la couture par laquelle un bac à sable à instantané entrera.
- **Un catalogue d'outils tenu par la plateforme** ([ADR 0014](../adr/0014-un-catalogue-d-outils-tenu-par-la-plateforme.md)) :
  des API tierces appelées POUR l'agent, clé côté serveur, plafonnées par run et inscrites au
  registre de coûts. L'agent ne choisit ni l'URL ni la méthode, et n'a aucun identifiant de
  fournisseur.
- **Une documentation anglaise** de déploiement et d'usage (`docs/`), dont les exemples YAML
  sont relus par le parseur du DSL et le modèle de politique — un test les garde.

## Ce que le banc du 2026-09-24 a montré

Deux tickets RH ont tourné l'après-midi avec de vrais agents. Ce que le cluster dit, et que
les 492 tests ne disaient pas :

1. **Les deux tickets sont morts sans que personne le voie.** RH-1 : workflow Temporal `FAILED`
   sur `collect_run_artifacts` — la garde « pas de dépôt » existe dans le code mais **l'image
   déployée ne l'avait pas**. RH-2 : `FAILED` sur heartbeat de `await_run` (`NO_RETRY`) parce
   qu'un `helm upgrade` a redémarré le worker pendant le run : **mettre la plateforme à jour tue
   les tickets en cours.** Et l'API ne lit jamais l'état Temporal : le ticket reste en `demande`,
   sans événement ni écran (P0-2).
2. **Le catalogue d'outils n'a toujours jamais atteint un agent** : non monté dans la démo,
   image runner sans `outils_locaux.py`, 0 ligne `kind=tool` au registre (P0-1).
3. **Le coût par run n'a jamais produit un chiffre non nul** — 12 lignes, tous à zéro, code
   compris : la démo tourne en passerelle directe, rien ne compte (P0-1).
4. **Le garde-fou d'écriture du runner ne s'applique pas à Claude Code** : son ACP n'envoie pas
   `kind`, et `guardrails.py` retombe en « lecture : autorisé ». 91 décisions, 0 refus (P0-3).
5. 5 `result.repair` sur 8 runs : le contrat `StageResult` n'est pas assez guidé (P1-6).

## Ce qui manque pour dire « en production »

0. **Une installation neuve est inutilisable, et la sécurité multi-locataire est décorative**
   (état des lieux, § A2–A4) : pas de création d'organisation, pas de jeton d'API, pas de premier
   admin ; `dev_login_enabled=True` par défaut et jamais posé par le chart ; RLS jamais armée ;
   mapping OIDC transverse aux orgs ; zéro métrique ; bus SSE en mémoire. C'est P0-3 à P0-5.
   **Avancement (soir du 2026-09-24)** : P0-2 livré (#23 — `await_run` rejoue, un ticket mort
   porte sa cause, l'API lit l'état Temporal) ; P0-3a livré (connexion de développement
   éteinte par défaut et refusée en prod, escalade `admin*` retirée, discovery OIDC + PKCE +
   `state` signé + redirection bornée, groupes `choregos:<org>:<groupe>` rattachés à LEUR
   organisation, rôles de projet indexés par `org/slug`, `resolve_project` qui refuse un slug
   ambigu, jetons d'API émis/expirés/révoqués par `/me/tokens`, et quatre recherches par slug
   seul corrigées — `add_member`, webhooks, orchestrateur, train) ; P0-3b livré (RLS
   **fail-closed** sur `app.current_orgs`, posée par le principal, `*` pour le jeton de run,
   les webhooks vérifiés et l'orchestrateur ; INSERT d'un projet jugé sur `org_id` ; l'API
   refuse un superutilisateur PostgreSQL en staging/prod et le PostgreSQL embarqué crée
   `choregos_app` sans SUPERUSER ; **quatre tests sur un vrai PostgreSQL**, en CI par un
   service) ; P0-3c livré (secret des webhooks non signés généré et injecté par le chart, plus
   de valeur par défaut publiée ; webhook GitHub refusé sans secret en staging/prod ;
   `rate_limit_per_minute` enfin réel — fenêtre glissante par adresse sur `/auth/*` et
   `/webhooks/*` ; `SECURITY.md` ne promet plus que ce qui existe ; garde-fous du runner :
   la nature d'une demande ACP sans `kind` est DÉDUITE du titre et des arguments — les
   écritures de Claude Code passent enfin par le périmètre —, la racine du workspace est
   retirée des chemins, la fiche d'accès compte les natures déduites).
   **Banc série `c` (soir, images P0-2)** : les trois tickets de code `done` avec de vrais
   agents ; les deux RH passent `sourcing` (les garanties `outputs_present` et
   `evidence_facts` ont accepté) puis **échouent en `qualification`** : l'agent ne trouve
   pas les profils, parce que le playbook lit `{{ spec }}` (un document logiciel) et que les
   sorties d'étape (`inputs: [profils]`) ne lui sont pas données — **défaut de contrat**,
   PR suivante. Le catalogue a été **listé** par les agents (10 `GET /tools`) mais jamais
   **appelé** (0 `POST`) : l'outil est annoncé dans le playbook, pas exigé par un mécanisme
   — une garantie `tool_called` lisant le registre serait le mécanisme.
   **P0-4a livré** : les métriques existent (voir S7-07). Reste P0-4b : bus d'événements
   sur LISTEN/NOTIFY (le SSE est muet dès deux répliques), `/readyz` qui teste Temporal,
   identifiant de requête et clés `project/work_item/run_id/stage` dans les journaux.
   **P0-4b livré** (bus sur `pg_notify` au commit + relais `LISTEN` par réplique, `/readyz`
   teste Temporal, `X-Request-Id`, clés dans les journaux). **P0-5a livré** : amorçage de la
   première organisation et de ses admins (`CHOREGOS_BOOTSTRAP_*`, chart `global.bootstrap`),
   `GET/POST /orgs`, `POST /projects/{id}/work-items` sur tracker interne (clé frappée par la
   plateforme, interpréteur démarré), `choregos-admin tokens create` pour le premier jeton,
   CLI : `orgs`, `tokens`, `items create`, `projects create` sans `--repo`, et ses premiers
   tests. **P0-5b livré** : les verdicts des garanties journalisés et affichés (#32) ; page de
   connexion, déconnexion, redirection sur 401, session et **sélecteur d'organisation**
   (projets adressés `org:slug`) ; assistant projet avec dépôt facultatif, templates depuis
   l'API, choix du tracker ; paramètres **éditables** (connecteurs avec leurs types, politique
   en YAML, profils de modèles) ; administration (membres, jetons d'API, backends,
   exécuteurs) ; suivi du provisioning ; « nouvelle demande » sur le board d'un tracker
   interne ; transcript et abandon de release ; fixtures hors du bundle de production ;
   `next-intl` retiré (l'anglais de l'interface = P1-1b). Reste : e2e Playwright contre
   l'API réelle (P1-3), a11y et budget de bundle (P2).
   **P1-2 livré** (#34) : SBOM + provenance sur les images, Trivy sur l'image de release,
   scan nocturne bloquant, gitleaks dans la porte, `tools/bump_version.py` (dépôt aligné sur
   v0.3.0, la release refuse un tag divergent), wheels attachés, couverture avec `apps/api`
   et `adapters` (seuil 70 % = 72 % mesurés). **P1-1a livré** : `docs/` est la référence,
   en anglais (getting started, deployment, usage, concepts, development, security,
   contributing, `cli.md` généré et testé) ; `docs/fr/` archive les guides français ; index
   ADR et runbooks avec résumés anglais. **`CLAUDE.md` → `AGENTS.md` était FAUX** : le même
   commit a remplacé le contenu d'`AGENTS.md` par un import de lui-même (onze octets), et
   `CLAUDE.md` étant un lien symbolique dessus, les instructions du dépôt étaient vides des
   deux côtés pendant deux jours — restauré et gardé par `tests/docs/test_instructions_des_agents.py`,
   qui refuse aussi une cible `make` inexistante. Reste P1-1b/c : l'anglais
   de l'interface, la traduction intégrale des 14 ADR et des 11 runbooks.
   **Le premier coût non nul du produit (2026-09-26, 18 h)** : sur le locataire dev de Diametral,
   un appel `platform/cheap` a répondu à travers la passerelle du locataire puis celle de la
   plateforme — 16 tokens, et la clé virtuelle `choregos-dev` porte **2,7 × 10⁻⁵ USD** de dépense,
   sous un plafond de 20 $ sur 30 jours. L'argument n°1 du produit avait douze lignes de registre
   à zéro depuis le premier jour ; il a maintenant un chiffre.
   Ce qui l'empêchait n'était pas ce que BLOCKERS disait. Les **dix** valeurs du magasin de secrets
   du locataire étaient absentes sauf une, et l'opérateur Infisical écrivait à leur place la chaîne
   littérale `<no value>` : dix pods `1/1`, Argo CD `Synced`, `/readyz` à 200, et des cookies de
   session signés avec une constante publique. Les neuf valeurs ont été posées par un Job qui les
   **génère dans le cluster** — elles n'ont transité par aucun journal. La garde ajoutée le même
   jour (refus de démarrage sur `<no value>`) rend ce mode de panne impossible à répéter.
   Ce qui reste à prouver : un **agent** qui consomme cette clé et une ligne `kind=tool` au
   registre. Le locataire n'a pas d'accès Anthropic, donc ce sera sur le backend `opencode`, jamais
   éprouvé sur un banc.

   **Édition communautaire et édition entreprise (2026-09-26)** : [ADR 0024](../adr/0024-deux-editions.md)
   rouvre D1 et acte deux éditions. Le cœur reste Apache-2.0 sous la Varga Foundation ; l'édition
   entreprise sera propriétaire, dans un dépôt privé séparé, éditée par Diametral. Le
   communautaire devient **mono-organisation** : `POST /orgs` refuse la seconde avec un 409 qui
   nomme la décision, et le refus tient à l'ÉDITION, pas à un plafond codé en dur — l'édition
   entreprise le lèvera sans forker le cœur. Le contrôle des droits passe avant celui de
   l'édition, pour qu'un développeur n'apprenne rien d'une limite qu'il n'a pas le droit de
   rencontrer. `GET /edition` répond. Ce qui sort est mince et aucune capacité *prouvée* n'est
   perdue : ce qu'on retire, c'est la promesse d'un multi-locataire inachevé (treize tables
   encore hors RLS).

   **La couture de greffons (2026-09-26)** : il n'en existait AUCUNE — `register()` était public
   mais rien n'importait jamais un module tiers, ce qui rendait le dépôt séparé impossible.
   `charger_les_greffons()` lit les points d'entrée `choregos.plugins` ; l'API et l'orchestrateur
   l'appellent au démarrage ; un greffon déclaré qui ne charge pas **arrête le processus**. Les
   tests écrivent un vrai `.dist-info` plutôt que de bouchonner la découverte.

   **Fuite d'audit entre organisations, trouvée le 2026-09-26** : `audit_log` était la
   dernière table de mutation **hors RLS**, et n'avait même pas de colonne d'organisation ;
   `GET /audit` renvoyait `select(AuditLog)` **sans aucun `where`**. Un `project_owner` d'une
   organisation lisait l'audit de toutes les autres. Corrigé : colonne `org_id` sur les 29 sites
   d'écriture (argument **obligatoire**, donc `mypy --strict` garde les suivants), politique RLS
   avec `WITH CHECK` distinct de `USING` (une connexion écrit une trace sans organisation, elle
   ne doit pas pour autant pouvoir en écrire pour une autre), et filtre de route sur les
   organisations où `audit:read` est **réellement** détenu. Deux tests sur vrai PostgreSQL, dont
   un qui échoue si le filtre saute. Le même travail a mis au jour un second défaut, consigné
   dans BLOCKERS : `is_platform_admin()` rend vrai pour tout `org_admin` de n'importe quelle
   organisation.
   **Banc série `d` (images de `main` après P0)** : les trois tickets de code `done` ; **la
   qualification RH réussit** — les profils du sourcing lui parviennent (#28) ; 5 verdicts de
   garanties journalisés et affichés ; 43 décisions de garde-fous **déduites** du titre ACP
   (toutes en périmètre) ; 0 réparation de résultat ; toujours **0 appel d'outil** pour 10
   listages du catalogue → **P1-6 livré** : garantie `tool_called` qui lit le registre (la
   démo l'exige sur le sourcing), contrat de résultat avec la forme des objets, outil
   `validate_result` pour que l'agent se vérifie avant de finir.
   **P1-3 (première tranche)** : matrice RBAC **générée** depuis `ROLE_PERMISSIONS` (5 rôles ×
   12 routes portant une permission, l'attendu se déduit, jamais écrit à la main) ; jetons
   de run : expiration, audience, émetteur, signature d'une autre paire ; migrations :
   `downgrade base` puis `upgrade head` ; contrats HTTP de LiteLLM (plafond envoyé, alias
   déjà pris, dépense, révocation, catalogue) et de Slack sur transport simulé ; les tests
   `live` tournent la nuit quand les secrets existent. Reste : Ecphoria, ArgoCD, Tekton,
   GitHub (scm, tracker, client), pgvector, rest, adf — et la couverture à 80 %.
   **P1-4 livré** : `pgvector` n'importe plus l'API (session et modèles **injectés**, la
   fabrique branchée par `choregos_api.adaptateurs` au démarrage de l'API et de
   l'orchestrateur) ; le cycle `runner ⇄ tools-mcp` est cassé (le client interne vit dans
   `choregos_tools_mcp.client`, le runner le réexporte) ; la CLI ne dépend plus de
   l'orchestrateur ; `CHOREGOS_TEMPLATES_DIR` remplace `parents[5]` — et **l'image ne copiait
   pas `templates/`** : aucun projet à template ne pouvait être provisionné depuis un pod,
   corrigé et gardé par un test. Deux constats de l'état des lieux se sont révélés faux à
   la lecture : les « trois clients GitHub » sont un client et deux adaptateurs qui
   l'emploient ; le « calcul cross-backend dupliqué » est une politique (choisir le
   relecteur) et une mesure (la comparer) — deux fonctions, pas une copie.
   **P1-5 livré** : PodDisruptionBudget pour le front et **par file d'attente** de
   l'orchestrateur (rendus seulement au-delà d'une réplique) ; anti-affinité `soft|hard|none`
   sur les trois composants ; pool de connexions **borné** (`max_overflow`, `pool_timeout`,
   `pool_recycle`, exposés par le chart) ; garde du chart qui **refuse le rendu** en
   staging/prod avec une dépendance embarquée, `devSecrets` ou `devLogin` ; runbook de
   sauvegarde Temporal (anglais) ; et l'egress par domaine **livré** au lieu de promis : le
   provisioning écrit un **Squid par projet** dans le namespace des runners (allowlist =
   `allow_domains`, sans root, en lecture seule, `CONNECT` vers 443 seulement), la
   NetworkPolicy n'ouvre Internet qu'à lui, chaque pod d'agent reçoit `HTTPS_PROXY`. Prouvé
   sur le kind local (`tests/cluster/test_egress_proxy.py` : 200 sur `api.github.com`, 403
   ailleurs) ; ce qui n'est PAS prouvé : un run Tekton complet derrière ce proxy (pas de
   Tekton sur le banc). Au passage : le test e2e des webhooks Jira/GitLab échouait seul
   depuis P0-3 (secret vide → 401) — le banc e2e en pose un.
   **P1-3 (deuxième tranche)** : contrats HTTP sur transport simulé pour tout ce qui restait
   nu — App GitHub (JWT, jeton d'installation ciblé et mis en cache, expiration), client
   GitHub (limites secondaires, 5xx, `retry-after`, GraphQL, pagination), SCM GitHub
   (branches, PR, brouillon → GraphQL, checks/relectures/fichiers, merge queue ou direct,
   50 annotations max), tracker GitHub (issues, labels scopés, commentaire de suivi
   réécrit, board Projects v2 avec création d'option, signature de webhook, événements),
   client REST (Retry-After, backoff borné, 204, pagination), Tekton (client Kubernetes,
   secret + PipelineRun, idempotence, états, annulation, journaux, CI, CloudEvents), Argo CD
   (santé, Rollout abandonné, fenêtres, promotion GitOps de bout en bout), Docker local,
   Ecphoria (tenant, disjoncteur après 3 pannes, `metadata`, propositions, recherche),
   passerelle directe, tracker interne, ADF, vecteur lexical. **Trois défauts trouvés par ces
   tests** : les journaux d'un pod Tekton (`text/plain`) étaient parsés en JSON et `logs()`
   ne rendait jamais rien ; la **première promotion** vers un environnement échouait toujours
   (le client GitHub lève sur 404, `_write_manifest` attendait `None`) ; la kustomization
   absente aussi. Couverture mesurée **78 %** (72 % avant), seuil relevé à 75.
   **P1-3 (troisième tranche)** : les routeurs que rien n'exerçait, par HTTP avec les fakes —
   tickets (création interne, liste, timeline complète, actions, décisions), runs
   (événements, `after_seq`, accès, diff en/hors périmètre, transcript), coûts (par nature,
   étape, jour, fenêtre, CSV avec BOM et budget, organisation par projet), trains (état depuis
   le workflow, départ/gel/dégel signalés et audités, approbation/abandon d'une release),
   webhooks (Tekton dédupliqué, Argo CD et Alertmanager sous secret partagé, GitHub sans
   secret hors prod), templates (lus du dépôt, créés, mis à jour, **chaque template livré
   passe le schéma**), plateforme (backends, exécuteurs, clés, 403 hors admin),
   provisioning, API interne au jeton de run, `choregos-admin`, `RealTemporal.describe()`
   sur client simulé, bus d'événements. **Un défaut trouvé** : le template livré
   `github-aca` déclarait `requires.azure_capabilities`, inconnu du schéma — servi depuis le
   disque, il aurait été refusé par l'API ; le schéma le porte. Couverture **80,1 %**, seuil
   à **80**. **`pgvector` sur base, enfin** : l'adaptateur n'avait jamais été exercé sur
   une base, et sur PostgreSQL il ne voyait **rien** — la fabrique lui donnait une session
   nue, et la RLS fail-closed du 2026-09-24 ne montre rien à une session qui ne nomme pas
   son organisation (« projet inconnu » partout ; SQLite, sans RLS, laissait passer). La
   fabrique exige l'organisation (`org`, ou `*` pour l'orchestrateur) et refuse sans ; le
   routeur mémoire de l'API lit le **connecteur `memory` du projet** au lieu de coder
   Ecphoria en dur (un projet en `pgvector` lisait Ecphoria ici et pgvector dans
   l'orchestrateur). Trois tests : cycle complet sur SQLite, refus sans organisation, et sur
   PostgreSQL deux organisations dont chacune ne voit que sa mémoire — la session nue
   d'avant y échoue, comme prévu.
   **P1-1c livré** : les 14 ADR et les 11 runbooks sont **en anglais** dans `docs/adr/` et
   `docs/runbooks/` (noms de fichiers inchangés : ce sont des identifiants, les alertes et
   les pages y pointent) ; les originaux français sont archivés dans `docs/fr/adr/` et
   `docs/fr/runbooks/`, plus maintenus. Les index ne parlent plus de « French ». Reste
   P1-1b : l'interface en anglais.
   **P3 (1–3) posés en ADR** : ADR 0015 (attestation de paternité IA signée, in-toto/DSSE,
   pack de preuves art. 50) et ADR 0016 (exécuteur `agent-sandbox` : pool tiède, suspension à
   travers une porte humaine, instantanés — ADR 0013 revisité) sont **proposés**, pas
   implémentés ; `docs/positioning.md` compare honnêtement à ax, agent-sandbox, OpenHands,
   Tembo, les produits fermés et les courtiers d'outils, et dit ce qu'ils font mieux.
   **P3 (4–10) posés en ADR** : 0017 (conformité MCP 2026-07-28 : transport sans état,
   `tasks` pour `get_ci_logs`/`ask_human`, elicitation à la place des questions en prose,
   OAuth lié à la ressource pour les serveurs MCP du catalogue), 0018 (OTel GenAI depuis le
   runner et LiteLLM, une trace par run, le registre reste la vérité du coût), 0019
   (Workflow Streams comme transport du journal d'un run, `run_events` reste l'archive —
   attend le serveur auto-hébergé), 0020 (évals de trajectoire scorées depuis le journal par
   des mécanismes, jamais par un juge), 0021 (SPIFFE derrière le jeton de run : révocation
   par suppression, mTLS vers la passerelle — option d'équipe plateforme), 0022 (catalogue
   fédérable : une entrée de registre devient une PR, née fermée), 0023 (constructeur visuel :
   la carte en lecture est livrée, les éditions seront des opérations sur le YAML). Tous
   **proposés**, aucun implémenté ; chacun dit ses conditions et ce qu'il refuse.
   **Reste du P2, différé à dessein** : le découpage de `stage.py` (723 l.), `interpreter.py`
   (775 l.), `services.py` (678 l.) et `schemas.py` (780 l.) — les seuils de complexité
   sont tenus (#51) et trois PR ouvertes (#39, #48, #50) touchent `stage.py` : découper
   maintenant, c'est trois conflits garantis pour un gain de lecture. À faire dans une PR
   seule après les fusions, l'interpréteur sous les historiques de replay (#48).
   **Déploiement de `main` fusionné sur le banc kind (2026-09-25, soir)** : images
   reconstruites, chart mis à niveau, six déploiements relancés — `/readyz`, `/healthz`,
   OpenAPI, 39 séries `choregos_*`, le web avec sa CSP à nonce ; aucune erreur au journal.
   LiteLLM embarqué attend le secret `platform-llm-key` (« pas de clé pour l'instant »), donc
   `helm --wait` expire : c'est le seul pod qui manque. **Deux défauts trouvés en regardant
   le namespace** : 64 secrets `run-*` (un jeton par run) survivaient à leur Job — le
   secret est désormais **rattaché au Job** (`ownerReferences`, posé après la création du
   Job) et le ramasse-miettes l'emporte avec `ttlSecondsAfterFinished` ; et le Role rendu
   par `gitops.py` pour le namespace d'un **projet provisionné** n'accordait ni `patch` sur
   les Jobs (la file d'admission y aurait échoué en silence) ni `update`/`patch` sur les
   Secrets — le banc tourne dans le namespace du chart et ne l'a jamais vu. Les deux Roles
   ont les mêmes verbes, et un test confronte désormais l'exécuteur au Role du projet comme
   il le faisait déjà au Role du chart.
   **Le hook de migration ne pouvait pas démarrer sur un vrai Argo CD (2026-09-26)** : le
   locataire dev était bloqué depuis **sept heures** sur `choregos-migrations-1`, Job sans
   aucun pod, et toute la Sync attendait derrière. Cause : le Job tournait sous
   `choregos-api`, une ressource **ordinaire** de la version, alors qu'un hook `pre-install`
   (que Argo CD traduit en PreSync) part avant toute ressource ordinaire — « error looking up
   service account choregos/choregos-api: serviceaccount not found ». Le chart documentait
   déjà ce piège pour Helm et l'avait contourné en passant le banc kind en `post-install`
   (`values/local.yaml`, base embarquée) : le **défaut par défaut** est donc resté, et seul un
   déploiement Argo CD réel pouvait le montrer. Corrigé à la racine : le Job a son **propre**
   compte de service, rendu par le même hook avec un poids plus faible (-10 contre -5), sans
   jeton monté (les migrations ne parlent qu'à la base) et portant les annotations de
   `global.serviceAccount` (une base en identité de charge de travail doit pouvoir s'y
   authentifier). Garde : `tests/charts/test_hook_de_migration.py`, qui rend le chart pour les
   quatre environnements et exige que le compte du hook soit dans le même hook et avant le Job
   — neuf cas, en Python, sans greffon Helm. **Vérifié sur le banc kind** (2026-09-26) : montée
   en chart **0.4.0**, révision Helm 13 « Upgrade complete », donc le hook `post-upgrade` a
   bien créé le compte puis joué le Job ; `alembic_version` = `b2d4f6a8c0e1` en base, et le
   Job a été nettoyé par sa propre politique (`hook-succeeded`). Reste, comme la veille, le
   seul LiteLLM embarqué en `CreateContainerConfigError` : il attend `platform-llm-key`, la
   clé de fournisseur qu'on n'a pas.
   **v0.4.0 publiée (2026-09-26)** : dix images multi-arch signées et le chart sur
   `oci://ghcr.io/vargafoundation/charts/choregos` — c'est ce chart que le locataire Diametral
   vendore. Un job de la release a échoué, et sur un vrai défaut : `choregos-playbooks`
   déclarait `packages` (qui embarque tout l'arbre) **et** un `force-include` sur `roles/`,
   donc hatchling refusait d'ajouter deux fois le même chemin. Rien ne le voyait — `make ci`
   ne construit aucun wheel, et l'espace de travail tourne en mode éditable où les gabarits
   sont simplement là. Corrigé, et tenu par `tests/paquets/` qui regarde **dans** le wheel :
   les onze gabarits de rôle et les six évals y sont, chaque chemin une seule fois, et la
   liste des `.md` du dossier source est comparée à celle du wheel plutôt qu'écrite à la main.
   **Le locataire dev est monté, et a livré un troisième défaut** : le hook de migration est
   passé (compte du hook créé avant le Job, migrations appliquées, Job nettoyé, synchro
   `Synced`), tous les pods sont venus — sauf l'API, en `CreateContainerConfigError` sur
   « couldn't find key generic-webhook-secret ». Le chart réclamait **en dur** sept clés du
   secret de l'API, dont cinq ne servent qu'à une fonctionnalité : une fonctionnalité non
   utilisée empêchait donc l'API d'exister. Règle posée et tenue par
   `tests/charts/test_cles_de_secret_optionnelles.py` (quatre environnements) : `session-secret`
   et `run-token-private-key` restent **exigées** (démarrer sans elles serait pire — cookies
   signés à vide, jetons émis par une paire éphémère) ; `run-token-public-key` (elle se déduit
   de la privée), les deux secrets de webhook et les deux clés d'App GitHub passent en
   `optional: true`, parce que le code refuse déjà à vide (`verify_shared_secret` rend False,
   le webhook GitHub refuse en staging/prod). Un troisième test interdit qu'une clé nouvelle
   arrive sans qu'on ait tranché son camp.
   **Le même défaut d'empaquetage dans trois paquets** : `core` (presets et gabarits du DSL) et
   `runner` (`versions.lock`) avaient eux aussi un `force-include` sur un chemin déjà couvert
   par `packages`. La release 0.4.1 est retombée dessus après la 0.4.0 — parce que le premier
   test ne construisait QUE le paquet qui avait cassé, et parce que « neuf wheels » ne voulait
   rien dire sans savoir combien en attendre. `tests/paquets/` construit désormais tout
   l'espace de travail (`uv build --all-packages`, la commande de la release), compte les
   wheels contre la liste des `pyproject`, vérifie dans CHAQUE wheel les fichiers de données
   lus sur le disque, et éprouve les `force-include` légitimes — ceux de `contracts`, dont les
   schémas vivent hors du module. Dix-neuf cas.
   **Et le greffon qui mentait** : `make charts-test` ne vérifiait que la *présence* de
   `helm-unittest`, pas sa version épinglée. Un 0.5.1 resté sur le poste déclarait rouge un
   test vert en CI (il traite un chemin JSONPath absent comme une erreur). La cible compare
   désormais la version, refuse de jouer si elle diffère, et dit comment la poser — un test
   dont le verdict dépend du poste ne vaut rien. La CI, elle, était juste : Helm 3.22 et le
   greffon 1.1.2 installés à chaque exécution.
   **Découpage de l'orchestrateur livré** : `stage.py` (847 l.) devient quatre modules —
   `plan` (le `StagePlan`), `stage` (la préparation : modèle, clé, contexte, `StageInput`),
   `execution` (lancer, attendre, abandonner), `bilan` (dépense, résultat, findings),
   `garde` (l'injection) — et `stage` réexporte tout, l'interpréteur et les tests lisent au
   même endroit. Les trois activités que le workflow portait en fin de fichier (`load_context`,
   `record_workflow_failure`, `signal_train`) et la lecture des erreurs Temporal passent dans
   `activities/interpretation.py` ; `interpreter.py` ne contient plus que le workflow. Aucune
   logique changée : 107 tests orchestrateur/replay/e2e verts, les quatre historiques rejoués.
   **Découpage de l'API livré** : `services.py` (678 l.) devient le paquet `services/` —
   `definitions` (workflow et politique actifs, défauts d'un projet neuf), `projets`,
   `tickets` (projections des tickets, runs, demandes, releases ; clé interne ; rangement des
   sorties), `couts`, `evenements` (`persist_event`), `memoire` (A/B) — et `schemas.py`
   (780 l.) le paquet `schemas/` en huit modules par domaine (base, identité, projets,
   définitions, tickets, livraison, mesures, plateforme). Les deux `__init__` réexportent
   tout : `choregos_api.services.X` et `choregos_api.schemas.X` restent l'adresse de tout,
   aucun import n'a changé ailleurs. Aucune logique changée : 112 tests API, 7 sur
   PostgreSQL, 115 orchestrateur/CLI/e2e/replay et 476 paquets verts.
   **P1-1b livré** : l'interface est **en anglais** — les 26 fichiers du front (pages,
   composants, libellés, `aria-label`, placeholders, messages d'erreur), `lang="en"`,
   formats `en-GB` (montants, dates, durées). Pas d'i18n : `next-intl` avait été retiré le
   2026-09-24 (« pas de demi-état ») et l'anglais est la langue de référence ; le français
   reste dans le code, les commentaires et les données de démonstration (`mocks/data.ts`).
   Tests vitest, e2e Playwright et `tsc`/`eslint` alignés. S5-01 passe ✅.
   **P2 front (a11y) livré** (#47) : `eslint-plugin-jsx-a11y` en mode strict (33 règles, zéro
   faute) ; axe dans Playwright sur six écrans en mode démo, bloquant sur serious/critical
   (une vraie violation trouvée : un `<dl>` sans `dt`/`dd`) ; parcours et axe joués en CI dans
   le job web, rapport en artefact sur échec. Pas encore : clavier sur le graphe de workflow.
   **P2 front (CSP et bundle) livré** (#52) : CSP à **nonce** pour les scripts (`'strict-dynamic'`,
   plus d'`unsafe-inline` — les styles le gardent, Next injecte des styles inline) posée par
   `src/proxy.ts`, rendu dynamique forcé pour que le nonce change à chaque réponse ; test e2e
   qui lit l'en-tête et vérifie qu'un script inline sans nonce ne s'exécute pas ; budget de
   bundle en CI (`pnpm bundle:check` : 1 600 kB au total, 600 kB par page ; mesuré 1 025 /
   224 kB). Reste P2 front : `design-system` publié sur npm/GHCR.
   **P2 front (clavier sur le graphe) livré** : les états du graphe de workflow se parcourent
   au clavier — Tab dans l'ordre de lecture (colonne par colonne, le DOM est trié comme la
   carte), ← → suivent les transitions (nominale d'abord), ↑ ↓ changent d'état, Début/Fin ;
   l'état sous le curseur est décrit sous la carte (`aria-live`, transitions, acteurs, gates),
   chaque nœud porte un `aria-label`, anneau de focus visible. Au passage : un état atteint
   seulement par une arête secondaire (question, escalade) se place après son origine au lieu
   de la première colonne ; le contrat OpenAPI des arêtes déclare enfin `kind`/`label`/`wildcard`
   que l'API émettait sans le dire ; le mode démo répond à la validation avec une carte, donc
   l'écran workflow est couvert par axe. Sept tests vitest, un parcours e2e.
   **P2 (Kyverno, RuntimeClass) livré** : la règle `non-root` est **stricte** — le contexte
   de sécurité du pod (non-root, seccomp) et des conteneurs (pas d'escalade, capacités
   retirées) est exigé, plus toléré s'il manque ; `tests/charts` rend le chart pour les quatre
   environnements et applique la règle à chaque pod (LiteLLM embarqué corrigé : il n'avait
   pas de contexte de pod ; l'image tourne en uid 1000, vérifié à l'import, pas encore en
   service sur le banc faute de clé) ; les pods d'agent (Job et PipelineRun) portent le
   profil seccomp au niveau du pod ; `runner.runtimeClass` (gVisor, Kata) s'applique à
   chaque pod d'agent quand la politique n'impose pas déjà gVisor, qui l'emporte toujours.
   **Banc séries `e`/`f` (2026-09-25, images de `main` après #37)** : la série `e` est morte
   sur un jeton OAuth révoqué (refait, kind seulement) ; la série `f` a montré **deux défauts
   réels** de la file d'admission (ADR 0013). 1) Deux runs RH ont « dépassé 20 min » **sans
   qu'un pod ait jamais tourné** : leur Job attendait une place, et l'attente en file
   comptait contre le budget de l'étape. 2) Les Jobs suspendus de ces runs morts sont restés
   en **tête de file** — plus personne ne relevait leur état, et `_admettre` n'admet que le
   plus ancien : cinq tickets figés derrière deux zombies, débloqués à la main
   (`kubectl delete job`). Corrigé : l'attente en file a sa propre borne (`FILE_MAX_MINUTES`,
   six heures) et ne consomme pas le budget ; tout run que `await_run` n'attend plus
   (budget dépassé, jamais admis) **retire son Job** et se marque `failed` ; les bornes
   Temporal couvrent la file. Trois tests. Ce que la série `f` prouve par ailleurs : la
   garantie `tool_called` **refuse** quand `verifier_adresse` n'est pas au registre (6
   verdicts journalisés), et le code (`DEMO-2f`) passe implement → verify avec le jeton neuf.
   Ce qu'elle ne prouve toujours pas : un appel d'outil réel par un agent (les runs RH n'ont
   pas tourné), ni le coût (mode `direct`).
   **Banc série `g` (2026-09-25 09:12–09:58, orchestrateur corrigé de #48 chargé dans kind)** :
   cinq tickets, onze runs, **zéro zombie** — la file s'est vidée seule, chaque run refusé
   s'est terminé, chaque ticket a fini en `needs_human` (validation de l'abandon) et il ne
   reste aucun Job dans l'espace de noms : le correctif de la série `f` tient sur le banc.
   `tool_called` refuse encore à raison (RH-1g/2g, `verifier_adresse` jamais au registre).
   Mais dix runs sur onze sont morts en `agent_silencieux` : **« OAuth access token has been
   revoked »** — le jeton copié dans `agent-creds` a été révoqué par la rotation locale de
   Claude Code pendant la série (seul `DEMO-1g` implement, parti à 09:12, a fini). Le mode
   `direct` avec ce jeton n'est **pas reproductible** au-delà d'une rotation : appel d'outil
   réel et coût restent non prouvés jusqu'à une clé de fournisseur dans LiteLLM.
   **Replay enfin réel** : quatre historiques du banc archivés dans `tests/replay/histories/`
   (`DEMO-2d` code jusqu'à `done`, `RH-1d` sourcing → qualification, `DEMO-1e` mort sur
   401, `RH-1f` jamais admis) et **rejoués** contre l'interpréteur courant — le test ne
   skippe plus. Les payloads Temporal sont en base64 : la clé de passerelle (en mode
   `direct`, le jeton OAuth lui-même) et les jetons de run y étaient ; ils sont caviardés
   à l'archivage, gitleaks passe sur le dossier, et l'URL de l'API interne y est mise en
   liste blanche par motif (faux positif `generic-api-key`).
   **P2 (garde-fous fermés) livré** : `sandbox.unknown_requests: allow|reject` dans la
   politique (`contract-change`, schémas et types régénérés) ; en `reject` — le preset
   `regulated` — une demande de permission dont le runner ne reconnaît pas la nature est
   **refusée** avec un message qui nomme `report_finding` et `request_scope_change` ; en
   `allow` (défaut) elle passe et le journal dit « filet, pas mur ». Ce qui est reconnu
   (lecture déclarée, écriture déduite) ne change pas. Pas encore : la détection d'injection
   de prompt (contenu de tickets marqué non fiable, motifs) — OpenHands l'a, pas nous.
   **P2 (détection d'injection) livré** : `choregos_core.injection` repère, lexicalement et
   en deux langues, les motifs grossiers (ignorer les instructions, réassignation de rôle,
   exfiltration du prompt, envoi vers une URL, script téléchargé, balises de prompt, charge
   encodée) dans ce que l'agent va lire — ticket, documents, souvenirs du context pack —
   **avant** le run ; `sandbox.prompt_injection: warn` (défaut) journalise un événement
   `security.injection_suspected` sur le ticket, `block` (preset `regulated`) arrête l'étape
   avant tout run avec la raison (le ticket est marqué mort, un humain relit), `ignore` ne
   regarde pas. Dix motifs positifs, cinq tickets ordinaires sans faux positif, trois modes
   testés sur l'orchestrateur. Ce que ce n'est pas : une compréhension — un filet contre
   les cas grossiers, comme chez OpenHands.
   **P2 (complexité) livré** : les règles `C901`, `PLR0912`, `PLR0913`, `PLR0915` sont
   **actives** (seuils 15 / 20 / 8 / 80 — l'état des lieux les trouvait toutes ignorées) ;
   sept fonctions dépassaient : `_check_references` (validateur) et `type_of` (générateur
   TS) découpés, `_execute` (provisioning) devenu une table d'étapes, le diagnostic « agent
   silencieux » sorti de `Runner.execute`, deux signatures larges assumées avec un `noqa`
   motivé. **Un défaut trouvé en découpant** : la boucle des acteurs du validateur relisait
   la variable de la boucle des transitions — l'escalade d'un acteur humain vers un acteur
   inconnu n'était jamais rapportée ; corrigé et testé. Le découpage des gros modules
   (`stage.py`, `interpreter.py`, `services.py`) reste à faire : ils passent les seuils.

1. **Ce que la démonstration mono-nœud ne prouve pas** : elle tourne avec un SCM factice, donc
   les garanties qui lisent un diff (`scope_respected`, `diff_size_max`, `no_secrets`) **refusent**
   au lieu de passer — c'est voulu depuis le 2026-09-23, une garantie aveugle valait pire que
   rien. Un ticket ne peut pas être rejoué sous le même identifiant (Temporal refuse un id
   terminé). Et le chemin RH n'a pas de base de profils : les agents bloquent, à raison.
2. **Ce qu'aucun agent réel n'a encore traversé.** Depuis le banc du 2026-09-23, quatre choses
   ont été livrées et ne sont vérifiées **que par des tests** : le catalogue d'outils, la file
   d'admission des runs, le projet sans dépôt, et les rôles métier. Le banc précédent avait
   trouvé dix-sept défauts qu'aucun test ne voyait ; il faut le rejouer avant d'annoncer que
   ces quatre-là tiennent.
3. **Le déploiement réel** : la pile de test sur kind est volontairement petite (un Temporal de
   développement, un Postgres simple). Les valeurs HA — Temporal à trois nœuds, CNPG à trois
   instances, Argo Rollouts — restent à régler sur un vrai environnement.
4. **Les runbooks restants** doivent être joués une fois en staging. Celui de restauration l'a
   été, et il était faux : c'est l'argument pour jouer les autres.
5. **Le chaos au-delà du nœud** : coupure réseau, disque plein. La perte d'un nœud est jouée
   (`tests/cluster/test_node_loss.py`) ; ce qui reste est le préavis d'éviction spot, que la
   plateforme subit au lieu de l'écouter.
6. **Ecphoria** : E-01 à E-14 sont livrés et poussés sur `main` amont. Ce qui en ressort et qui
   nous concerne : le banc au profil Choregos (20 000 faits, 200 000 événements, 50 lectures/s)
   mesure la recherche à ~250 ms p50 / ~540 ms p95 sur une station de travail, au-dessus de la
   cible de 300 ms p95 ; `retrieval_scan_cap` à 512 ramène p95 à 24 ms sans coût de rappel
   mesurable à cette taille de corpus. À décider au provisionnement, pas en production.

7. **Le moteur, et ce qui le lie encore au logiciel** ([ADR 0012](../adr/0012-le-moteur-n-est-pas-lie-au-logiciel.md)) :
   sur les cinq attaches écrites le 2026-09-23, **trois sont tombées** le lendemain — dépôt
   facultatif, preuves nommées par le métier, rôles ouverts. Restent `StageOutputs`, typé pour
   le développement et seulement vérifiable en présence, et le déséquilibre des garanties, dont
   la plupart ne savent lire qu'un diff de code. Ni l'une ni l'autre ne bloque un métier ; elles
   sont laissées écrites plutôt que corrigées par une abstraction dont personne n'a besoin.

8. **Ce que la répartition des files ne prouvait pas, et ce qu'elle prouve depuis le 2026-09-27.**
   `worker.py` déclarait depuis le premier jour quelle activité va sur quelle file — « les appels
   externes lents sont isolés pour ne pas bloquer la boucle de décision » — et **rien ne
   l'appliquait** : `workflow.execute_activity` sans `task_queue` planifie sur la file du
   *workflow*. Tout tournait donc sur `orchestrator` ; les trois déploiements de workers
   spécialisés que le chart crée ne recevaient jamais une tâche. Aucun test ne pouvait le voir,
   les trois suites Temporal montant un worker unique portant toutes les activités : il répond
   quelle que soit la file demandée. Ce qui l'a rendu visible est une politique réseau de
   Diametral qui, elle, prenait la répartition au sérieux.

   Ce que la correction prouve : `apps/orchestrator/tests/test_repartition_des_files.py` lit dans
   l'historique Temporal la file sur laquelle chaque activité a été planifiée — `prepare_stage` et
   `start_run` sur `executor`, `mirror_state` sur `tracker`, `load_context` sur la file du
   workflow. L'historique porte cette information dès la planification, avant qu'un worker ait pris
   la tâche : l'affirmation ne dépend donc pas de ce qu'un worker écoute. Les trois suites montent
   désormais **un worker par file**, et la file du workflow n'y porte que les activités sans file
   déclarée — sans quoi un routage cassé y tournerait quand même.

   Ce que ça ne prouve pas : rien ne mesure que l'isolation *sert* à quelque chose. Personne n'a
   montré qu'une saturation de la file `tracker` laissait la boucle de décision réactive ; c'est
   la raison d'être invoquée par le commentaire, et elle reste une intention. En production,
   `orchestrator` garde par ailleurs toutes les activités — un filet pour ne pas laisser une tâche
   orpheline pendant une montée de version — donc un routage qui régresserait en production
   dégraderait au lieu de casser. C'est le test qui l'interdit, pas le déploiement.

9. **Le premier run d'agent réel, et les six obstacles qu'il a révélés** (nuit du 2026-09-27).
   Aucun n'était visible en test ; chacun a été trouvé en levant le précédent. Dans l'ordre :

   | Obstacle | Ce que ça donnait | Où c'est corrigé |
   |---|---|---|
   | un ticket naissait dans son état **terminal** | workflow `COMPLETED` en 1,5 s, zéro étape, board vert | #105 (contrat `initial`) |
   | les activités n'allaient jamais sur leur file déclarée | `start_run` sur `orchestrator`, `ConnectTimeout` vers l'API Kubernetes | #106 |
   | le ticket enregistrait `"Application error"` | la cause réelle ne vivait que dans le journal | #106 |
   | le Role `job-runner` de la plateforme manquait 3 verbes | Job créé, puis `403` sur `PATCH secrets` | infra#750 |
   | le pod d'agent recevait un proxy d'egress **inexistant** | premier appel mort sur « Name or service not known » | #110 + deploy#13 |
   | opencode **refusait sa configuration** | `initialize` en délai dépassé, stderr jamais lu | #111 (le diagnostic) puis le correctif de schéma MCP |

   Ce que ça prouve : la chaîne ticket → interpréteur → file `executor` → API Kubernetes → Job →
   clé virtuelle de passerelle fonctionne de bout en bout sur un locataire réel, sous Kyverno en
   Enforce et `clusterResourceWhitelist: []`. Le Job d'agent est créé, son secret lui appartient,
   sa clé est émise puis révoquée.

   Ce que ça ne prouve **pas** : aucun agent n'a encore produit un résultat, le registre n'a
   toujours aucune ligne `kind=tool`, et le coût du produit reste à 2,7 × 10⁻⁵ USD — un appel de
   sonde, pas un run. Les deux preuves du blocage 4 restent ouvertes.

   La leçon de méthode : **cinq des six obstacles ont été trouvés en rendant une panne lisible,
   pas en lisant du code.** Le seul qu'un test aurait pu attraper — la répartition des files —
   était invisible parce que les trois suites Temporal montaient un worker unique portant toutes
   les activités. Un décor qui répond toujours ne prouve rien.

10. **Les deux preuves du blocage 4 sont acquises** (2026-09-27, 19:05 UTC, locataire dev).

    Ce que la base dit, et c'est la seule chose qui compte :

    | `kind` | fournisseur | modèle / outil | lignes | € |
    |---|---|---|---|---|
    | `model` | `openai` | `platform/cheap` | 6 | **0,045872** |
    | `tool` | `github` | `lire_un_depot` | 1 | 0,000000 |

    Plus l'événement `choregos.tool.called` — outil `lire_un_depot`, fournisseur `github`,
    **code 200** : la plateforme a appelé l'API tierce pour le compte de l'agent, avec sa propre
    clé, et l'a compté. Le run porte `status: succeeded`, `cost_usd: 0.028728`, 30 858 jetons en
    entrée, 1 241 en sortie, et son résultat dit « Plan d'implémentation rédigé à partir des
    métadonnées du dépôt ».

    La chaîne prouvée, de bout en bout, sur un locataire sous Kyverno en Enforce et
    `clusterResourceWhitelist: []` : ticket → interpréteur → file `executor` → API Kubernetes →
    Job d'agent → opencode → passerelle (coût réel) → serveur MCP d'outils → API interne →
    GitHub (200) → registre de coûts.

    La ligne `tool` est à 0,00 € parce que cet outil est gratuit. C'est la LIGNE que `tool_called`
    exige, pas son montant ; le montant non nul est du côté `model`.

    **Ce que ça ne prouve pas.** Un seul outil, gratuit, sur un FQDN déjà ouvert : rien n'est
    prouvé d'un outil payant ni d'un fournisseur qu'il faut autoriser au réseau. Un seul backend
    (`opencode`) et un seul modèle (`platform/cheap`). Aucune garantie de périmètre n'a été
    éprouvée — le projet n'a pas de dépôt, donc pas de diff. Et le prix est une **copie** des
    tarifs de la passerelle plateforme, recopiée dans les valeurs du locataire : rien ne la
    resynchronise.

11. **Deux de mes propres changements de cette nuit étaient en trop, et c'est écrit ici.**
    En croyant découvrir que l'exécuteur `k8s_job` ne servait pas le catalogue, j'ai rendu
    l'annonce de l'URL conditionnelle (#112) puis ajouté un sidecar au Job (#113). Or
    `runner/outils_locaux.py` sert déjà ces outils quand personne ne le fait — son en-tête décrit
    exactement le défaut que je pensais trouver. Ma condition a donc retiré une annonce **vraie**,
    et le sidecar rétablit à grands frais ce que le runner faisait seul. Corrigé.

12. **Couture C3 : livrée à moitié, et la moitié non livrée est un refus argumenté.**

    Le plan demandait `IdentityProvider` et `TenancyPolicy` en `Protocol`, pour que l'édition
    entreprise substitue sans forker `auth.py` ni `deps.py`. En regardant les points de décision
    réels, il n'y en avait qu'un de substituable.

    **La tenancy n'a pas besoin de protocole** : la seule décision est « une seconde organisation
    peut-elle exister ? », et elle passe déjà par `edition.est_entreprise()`. L'édition entreprise
    appelle `declarer(ENTREPRISE)` depuis son greffon (couture C1) et la garde s'ouvre — sans
    toucher à `projects.py`. La couture existe donc, elle s'appelle `edition.declarer`. Ce que
    l'entreprise ajoute par-dessus (suspension d'organisation, plafonds par organisation, RLS sur
    les treize tables restantes) sont des routes et des colonnes **nouvelles**, pas des
    substitutions : un `TenancyPolicy` n'aurait eu qu'une implémentation et qu'un appelant.

    **L'identité en a besoin pour un seul point** : la traduction groupes d'IdP → rôles par
    organisation. Le cœur sait faire un préfixe (`choregos:<org>:<role>`) ; l'entreprise en veut un
    par organisation, et des groupes venus de SAML ou de SCIM (§2.2). C'est livré par
    `edition.declarer_le_mappeur_de_groupes`, avec un test qui vérifie que le mappeur déclaré est
    **réellement appelé par la connexion** — sans quoi ce serait un réglage décoratif. Le reste de
    l'identité d'entreprise (SAML, SCIM, révocation de session, ré-authentification forte) sont des
    routes nouvelles.

    Pourquoi c'est écrit ici plutôt que construit : un protocole à une implémentation est le genre
    d'abstraction que ce plan dit lui-même de refuser (§8, et la même leçon que C2, où les gates
    étaient déjà extensibles). Le jour où l'édition entreprise existe et veut autre chose, elle le
    demandera avec un cas précis — et ce sera moins cher que de deviner aujourd'hui.

13. **La traversée COMPLÈTE est prouvée sur le locataire dev** (2026-09-27, 22:19 UTC, chart 0.8.3).

    Le §10 prouvait les deux lignes du registre. Celui-ci prouve le parcours entier d'un ticket :

    `inbox` → l'agent appelle le modèle **et l'outil**, écrit `outputs.rapport`, la garantie
    `outputs_present` **accepte** → `en_cours` → un humain approuve par
    `POST /work-items/{id}/decisions` → **`fini`**.

    Le rapport de l'agent, mot pour mot : « Branche par défaut : main. Langue principale : Python.
    Issues ouvertes : 1. » Les trois faits sont **exacts**, vérifiés contre
    `GET /repos/VargaFoundation/choregos` après coup. L'agent n'a rien inventé : il a appelé
    l'outil, lu la réponse, et rapporté. Run `succeeded`, `cost_usd 0.017137`.

    Registre à la fin de la nuit : `model/openai` **10 lignes, 0,131805 €**, 243 303 jetons en
    entrée et 9 949 en sortie ; `tool/github` **5 lignes** ; **5** événements
    `choregos.tool.called`.

    **Deux tickets sont `fini`, et ils résument la nuit.** Le premier est né `fini` — c'est le
    défaut `jsonb` du §10, un ticket clos en 1,5 seconde sans avoir rien fait. Le second a
    réellement traversé. Le même état, deux vérités opposées : voilà pourquoi ce dépôt exige un
    test qui échoue en l'absence de la garantie, et pas une capture d'écran verte.

    **Ce qui a échoué en route, et c'est un bon échec** : au premier essai avec l'outil,
    `outputs_present` a REFUSÉ. L'agent avait mis son texte dans `summary` au lieu de
    `outputs.rapport` ; le ticket a retenté une fois puis escaladé en `needs_human`, exactement
    comme `on_fail` le déclare. La garantie a fait son travail — et c'était ma consigne qui
    nommait une intention là où un petit modèle suit une **forme**. Corrigée en donnant le JSON
    exact, l'étape est passée.

    **Ce que ça ne prouve toujours pas** : un outil payant, un fournisseur qu'il faut ouvrir au
    réseau, plus d'un backend, plus d'un modèle, et aucune garantie de périmètre (ce projet n'a pas
    de dépôt, donc pas de diff). Sept tickets restent en `needs_human` : les débris de la nuit de
    débogage, gardés exprès — ils portent chacun le message qui les a tués.

14. **L'image EE construite FROM l'image CE : le geste évident tournait en `community`**
    (2026-09-28).

    La #129 s'arrêtait avant l'image, Docker ne répondant plus. Essayée sur
    `choregos-api:0.8.3` : `RUN pip install --no-deps choregos_ee-*.whl` **réussit**, l'image
    démarre, et `GET /edition` répond `community`. Le venv, créé par uv, n'a pas de `pip` ;
    celui du PATH est celui du système, qui a posé la roue dans `~/.local`. Seule la garde de
    démarrage l'attrape, et seulement si l'exploitant a écrit `global.edition: enterprise`.

    L'étage commun de `docker/api.Dockerfile` déclare désormais `PIP_PYTHON` vers le python du
    venv : le geste évident devient le geste juste pour les deux images (API et orchestrateur),
    sans que le dépôt privé connaisse la disposition interne de l'image CE.

    **Ce que ça prouve** : `tests/paquets/test_image_ee.py` lit le Dockerfile (dans `make ci`,
    rouge sans la ligne — vérifié) ; et, avec Docker et `CHOREGOS_IMAGES_CE`, construit une vraie
    image FROM une image CE, puis exerce `create_app()` et le chargement des activités de
    l'orchestrateur avec `CHOREGOS_EDITION=enterprise`. Vert sur les deux cibles construites
    depuis ce commit ; **rouge sur `choregos-api:0.8.3`**, avec la garde de démarrage pour message.
    L'image CE démarre toujours en `community`.

    **Ce que ça ne prouve pas** : la CI ne joue que la garde statique — sur `main` elle construit
    en multi-plateforme et pousse sans charger, donc le test Docker n'y tourne pas. Les images
    publiées jusqu'à la 0.8.3 comprise ont le piège ; `docs/development.md` donne le contournement
    (`pip --python /app/.venv/bin/python`). La matrice de compatibilité EE↔CE et le dépôt
    `choregos-ee` restent à faire.
15. **Couture des routes : un greffon peut servir des routes, et seulement en AJOUTER**
    (2026-09-28).

    Un greffon savait enregistrer un connecteur, une garantie, une édition, un mappeur de groupes —
    pas servir une route : `create_app()` n'incluait que les siens. `greffons.declarer_un_routeur`
    comble ce manque ; les routes sont incluses sous `/api/v1` après celles du cœur, et gardées par
    les dépendances d'authentification du cœur comme n'importe quelle autre.

    **Trouvé en route** : depuis FastAPI 0.141, `app.routes` ne montre plus les routes incluses (un
    `_IncludedRouter` opaque). La première version de la garde de recouvrement les y cherchait et ne
    voyait rien ; le test témoin « sans greffon, pas de route » était vert à vide. La garde lit
    désormais les routeurs du cœur eux-mêmes, et le témoin l'OpenAPI.

    **Ce que ça prouve** : `test_greffons_routes.py`, avec un greffon installé pour de vrai (un
    `.dist-info` sur disque) — route servie, `401` anonyme et `200` connecté, présence dans
    l'OpenAPI, recouvrement de `GET /api/v1/orgs` refusé au démarrage. Garde de recouvrement
    retirée, puis inclusion retirée : rouge chaque fois.
    **Ce que ça ne prouve pas** : un routeur imbriqué est refusé, pas pris en charge ; aucun
    greffon réel ne s'en sert encore (l'édition entreprise, pour EE-1).
16. **Les migrations sont dans la roue, et un greffon peut apporter son schéma** (2026-09-28).

    La roue `choregos-api` 0.8.3 ne portait **aucune** migration (`apps/api/migrations`, hors du
    paquet) : personne ne pouvait migrer une base avec le cœur publié, et une édition entreprise
    testée contre ses roues ne pouvait pas monter un vrai PostgreSQL. Elles vivent désormais dans
    `choregos_api/migrations`, et `python -m choregos_api.migrer` — la commande du Job du chart et
    du banc de cluster — les trouve où qu'on la lance, au lieu d'exiger `/app/apps/api`.

    Un greffon déclare ses révisions dans le groupe `choregos.migrations` ; elles forment une
    branche Alembic (`depends_on` une révision du cœur) jouée par `upgrade heads`. Il crée SES
    tables et ne touche pas à celles du cœur.

    **Ce que ça prouve** : `test_migrer.py` — le cœur migré depuis un répertoire quelconque ; la
    branche d'un greffon installé pour de vrai jouée après le cœur, puis redescendue seule ; un
    emplacement déclaré mais absent refusé. `test_distribuables.py` compte chaque révision dans la
    roue (rouge quand on les exclut). La fixture PostgreSQL passe par la même configuration :
    `test_rls_postgres.py` éprouve donc en CI la commande qu'on déploie.
    **Ce que ça ne prouve pas** : le Job du chart n'a pas tourné sur un cluster avec la nouvelle
    commande (`tests/cluster` le fera en nocturne) ; aucun greffon réel n'apporte encore de schéma.
17. **Couture d'admission : un greffon peut refuser qu'un run démarre** (2026-09-28).

    Le cœur borne la dépense par run et la concurrence par exécuteur ; il ne savait rien d'une
    limite qui dépend d'autre chose — organisation suspendue, plafond mensuel, gel. Un greffon la
    déclare (`choregos_core.admission.declarer_une_admission`) ; `prepare_stage` la joue avant
    d'émettre la clé et de créer le run, et après le chemin de rejeu.

    **Ce que ça prouve** : `test_admission.py`, greffon installé pour de vrai — le contrôle reçoit
    organisation, projet, ticket, run et budget ; un refus arrête l'étape sans reprise
    (`admission_refused`, non rejouable), sans clé ni run en base, avec une raison qui nomme le
    contrôle ; un run déjà préparé n'est pas refusé au rejeu ; une panne du contrôle remonte et se
    retente au lieu de passer pour un refus. En négatif : appel retiré → rouge ; appel placé avant
    le chemin de rejeu → rouge.
    **Ce que ça ne prouve pas** : aucun événement dédié sur le ticket (il faudrait un `EventType`,
    donc une PR `contract-change`) — la raison passe par l'échec de l'activité, comme la garde
    contre l'injection. Aucun greffon réel ne s'en sert encore.

18. **La RLS couvre ce qui se rattache à un projet, et plus aucune table n'y échappe sans raison**
    (2026-09-28, [ADR 0026](../adr/0026-ce-que-la-rls-du-coeur-couvre.md)).

    `run_events`, `deployments` et `gateway_keys` portaient des données d'une organisation hors
    RLS — le journal d'un agent, les mises en production, les plafonds de dépense. Elles passent
    sous RLS forcée par leur projet. Les catalogues de l'instance restent ouverts, par décision.
    L'identité (`organizations`, `users`, `memberships`, `api_tokens`) reste hors RLS **à dessein** :
    le principal est résolu avant que la portée soit posée, et `exiger_admin_de_plateforme`
    compterait les seules organisations de l'appelant — l'administrateur d'une seule deviendrait
    administrateur de l'instance. C'est le travail de l'édition entreprise.

    **Ce que ça prouve** (PostgreSQL, rôle non superutilisateur) : une session bornée à `a` ne
    voit que les lignes de `a` dans les trois tables, une session sans portée n'en voit aucune ;
    elle ne peut pas écrire un événement sur le run de `b` ; et `test_chaque_table_est_sous_rls_ou_exemptee`
    refuse toute table ni sous RLS forcée ni exemptée avec sa raison. Migration retirée : les
    trois rougissent. Suites API et orchestrateur vertes avec PostgreSQL disponible.
    **Ce que ça ne prouve pas** : la charge — une fonction par ligne sur `run_events`, dont les
    lectures filtrent déjà par run ; aucune mesure de latence sur un gros journal.

19. **Coutures d'identité : révoquer une session, accorder l'administration de la plateforme,
    exiger une authentification fraîche** (2026-09-29).

    Une session signée valait jusqu'à `exp`, sans recours ; un greffon peut désormais la refuser à
    chaque requête (`declarer_une_validation_de_session`) — c'est le chemin de la révocation côté
    serveur et du déprovisionnement SCIM. Il peut aussi ACCORDER l'administration de la plateforme
    en plus de la règle du cœur, jamais la retirer. La session porte `iat`
    (`Principal.authentifie_le`), et `auth/login?reauth=1` fait redemander l'IdP.

    **Ce que ça prouve** : `test_coutures_identite.py`, greffon installé — `iat` posé ; session
    révoquée → 401 avec la raison à la requête suivante ; validation en panne → l'exception remonte ;
    un greffon accorde `/platform/*` à qui le cœur le refuse, sans retirer le droit à l'admin des
    deux organisations ; `reauth=1` → `prompt=login&max_age=0`, absents sinon. Chacune des trois
    coutures retirée : rouge.
    **Ce que ça ne prouve pas** : aucun IdP réel n'a honoré `prompt=login` devant nous ; le front
    ne propose pas encore le chemin de ré-authentification.

20. **Contrôle des gestes humains : un greffon peut refuser un projet de trop, ou une approbation
    sans authentification fraîche** (2026-09-29).

    `greffons.declarer_un_controle_de_geste` sur `project.create`, `workitem.decision` et
    `release.approve`, joué après les droits du cœur et avant que la route agisse. Réponse choisie par
    la nature du refus : 403, 409, ou 401 qui renvoie vers `auth/login?reauth=1`.

    **Ce que ça prouve** : `test_controle_des_gestes.py`, greffon installé — un projet de trop est
    refusé en 409 et n'est pas créé ; une décision sans authentification fraîche est refusée en 401
    et la demande n'est pas tranchée, puis acceptée avec une session récente (`iat` à quelques
    secondes) ; l'approbation d'une release passe par le même contrôle et n'est pas enregistrée.
    Chacun des trois appels retiré : rouge.
    **Vérifié qu'il n'y a pas de contournement** : la route web est le SEUL chemin qui tranche une
    demande. Les boutons Slack sont des liens vers elle ; les commentaires `/choregos approve` sont
    analysés (`github_events.parse_command`) mais le workflow ne les traduit en rien (`_absorb`) —
    une fonctionnalité annoncée et inerte, déposée en finding. Le jour où elle sera branchée, elle
    devra passer par `controler`.

21. **Terminer une connexion : un autre protocole ouvre une session exactement comme OIDC**
    (2026-09-29).

    La fin du callback OIDC (utilisateur, rôles par le mappeur en service, trace `auth.login`,
    cookie signé avec `iat`, redirection bornée) devient `routers.auth.terminer_la_connexion` /
    `ouvrir_la_session`, que le callback lui-même emploie. Un protocole apporté par un greffon —
    SAML, pour l'édition entreprise — ouvre donc la même session, sans second chemin écrit à la main.

    **Ce que ça prouve** : `test_coutures_identite.py` — une route de greffon qui appelle
    `terminer_la_connexion` pose une session avec `iat`, donne le rôle tiré du groupe, trace
    `auth.login` avec son canal, et borne une cible externe (rouge quand le bornage est retiré) ; les
    tests OIDC existants passent sur le callback refactoré.
    **Ce que ça ne prouve pas** : le cœur ne vérifie RIEN de l'identité qu'on lui passe — c'est à
    l'appelant (signature d'assertion, audience, fraîcheur), et c'est écrit dans la fonction.

22. **Deux findings refermés : un test qui attendait un instant, un commentaire qui mentait**
    (2026-09-29, #135, #138).

    `test_hotfix_uses_express_lane` attendait l'état `collecting` après l'approbation ; il constate
    désormais l'EFFET — la production promue dès le départ (hors horaire), aucun canary avant
    l'approbation, le canary suivi après. Sans le signal d'approbation, il rougit. L'échec d'origine
    n'a pas pu être reproduit (12 exécutions isolées, 12 en parallèle, deux suites complètes) : ce
    correctif retire la course, il ne prouve pas l'avoir vue.

    Le commentaire de demande humaine invitait à répondre par `/choregos approve` ; ces commentaires
    sont analysés mais jamais traduits en décision — l'humain répondait, et le ticket restait bloqué
    sans un signe. Il renvoie désormais vers l'interface, et S3-03 passe à 🟡.

23. **Les webhooks n'acheminaient RIEN sur PostgreSQL — et celui de Tekton n'était pas authentifié**
    (2026-09-29).

    Trouvé en recensant les chemins qui lisent la base sans portée. Les routes de webhooks
    (GitHub, Tekton, Argo CD, Alertmanager, Jira, GitLab) prenaient une session SANS portée ; la RLS
    fail-closed leur cachait tous les projets, et chaque événement était jeté comme « sans projet
    connu ». Les tests d'API tournent sur SQLite, sans RLS : personne ne l'a vu. Sur le locataire
    dev (PostgreSQL), aucun événement de tracker, de CI ou de CD n'a donc jamais atteint un ticket —
    la traversée du §13 passait par le tracker interne et l'API interne, pas par un webhook.

    Correctif : `deps.DbPlateforme`, une session de portée `*`, pour les routes authentifiées par
    signature ou secret. En l'écrivant, le webhook Tekton s'est révélé SANS aucune vérification :
    inoffensif tant que la RLS lui cachait tout, un chemin d'écriture anonyme dès qu'il voit les
    projets. Il exige désormais le secret partagé, en en-tête ou dans l'URL du puits (`?jeton=`,
    Tekton n'ajoutant pas d'en-tête), et refuse hors développement quand il n'est pas configuré.

    **Ce que ça prouve** : `test_rls_postgres.py::test_un_webhook_achemine_son_evenement_sous_rls`
    — sur PostgreSQL, rôle non superutilisateur, un événement Tekton atteint son ticket (0 avant le
    correctif) ; `test_routeurs_nus.py` — Tekton refuse sans secret ou avec un faux, accepte en
    en-tête et dans l'URL, refuse en production sans secret configuré (rouge quand la vérification
    est retirée).
    **Ce que ça ne prouve pas** : seul Tekton est éprouvé sur PostgreSQL ; les autres routes prennent
    la même session et vérifient leur secret avant de lire, sans test PostgreSQL chacune. Le puits
    CloudEvents du locataire doit recevoir `?jeton=` à la mise à jour.

24. **Toute la suite de l'API sur PostgreSQL : deux défauts de plus, dont une élévation de privilège**
    (2026-09-29).

    Après les webhooks (§23), la question était « combien d'autres ? ». `CHOREGOS_TEST_SUITE_SUR_POSTGRES=1`
    fait tourner les 171 tests de l'API sur PostgreSQL, schéma migré par `migrer`, rôle non
    superutilisateur ; la CI le fait désormais à chaque PR, après la passe SQLite.

    - **Un rôle de projet devenait un rôle d'organisation.** Le principal était résolu avant que la
      portée soit posée ; la jointure sur `projects` (sous RLS) ne voyait aucun projet, et une
      appartenance DE PROJET arrivait sans projet — donc comme un rôle sur toute l'organisation. Un
      développeur invité sur un projet lisait les autres. L'identité se résout désormais en portée de
      plateforme, puis la session est bornée.
    - **`POST /orgs` rendait 500** : la trace d'audit de la nouvelle organisation était refusée par la
      RLS d'`audit_log`, la session étant bornée aux organisations EXISTANTES de l'appelant — le geste
      même que l'édition entreprise déverrouille.

    Deux tests de sécurité attendaient des réponses propres à SQLite (403, « slug ambigu ») ; sous la
    RLS, PostgreSQL répond 404 et ne voit que le projet de l'appelant — il ne révèle même pas l'autre.
    Ils admettent désormais les deux, et exigent dans les deux cas de ne jamais rendre le projet d'une
    autre organisation.

    **Ce que ça prouve** : `test_rls_postgres.py` — un rôle de projet reste un rôle de projet ; une
    seconde organisation se crée (201) ; chacun rouge quand son correctif est retiré. La suite entière
    de l'API passe sur PostgreSQL (171) et sur SQLite.
    **Ce que ça ne prouve pas** : la suite de l'orchestrateur tourne encore sur SQLite seulement ; ses
    sessions sont toutes de portée `*` (activités de la plateforme), ce qui réduit le risque sans
    l'exclure.

25. **L'identité sous RLS : les 25 tables sont couvertes ou exemptées avec leur raison** (2026-09-29,
    ADR 0026 mis à jour).

    `organizations`, `memberships`, `users`, `api_tokens` passent sous RLS forcée : une organisation
    ne voit ni le nom ni les membres d'une autre ; un utilisateur est visible des organisations dont
    il est membre, et toujours de lui-même (`app.current_user`). Ce qui porte sur l'instance se lit
    dans une portée de plateforme explicite et restaurée (`en_portee_de_plateforme`) : le compte des
    organisations qui décide de l'administrateur de la plateforme, la recherche d'un invité par
    e-mail. Créer une organisation passe en portée de plateforme une fois le droit établi.

    Condition levée depuis l'ADR 0026 : le principal se résout en portée de plateforme (0.10.1), et
    la suite entière de l'API tourne sous RLS en CI. Sans ce filet, ce changement aurait été une
    fermeture à l'aveugle.

    **Ce que ça prouve** (PostgreSQL, rôle non superutilisateur) : une session bornée à `a` ne voit
    que le nom, les membres, les utilisateurs et les jetons de `a`, une session sans portée rien ;
    l'admin d'une organisation sur deux ne crée pas d'organisation (rouge si le compte n'est pas fait
    sur l'instance) ; inviter un membre d'ailleurs ne crée pas de doublon (rouge sans la portée de
    plateforme) ; un utilisateur sans appartenance lit son compte et crée un jeton (rouge sans
    `app.current_user`). Suite entière de l'API : 176 verts sur PostgreSQL.
    **Ce que ça ne prouve pas** : le compte des organisations dans `/audit` n'a pas de test qui échoue
    en son absence — la RLS d'`audit_log` masque déjà les lignes de plateforme ; il reste tel quel,
    commenté, et aucun code non éprouvé n'a été ajouté pour lui. L'édition entreprise doit passer
    SAML en portée de plateforme avant d'accepter la 0.11 (sa plage s'arrête à `<0.11`).

26. **Le voisinage : un quota de namespace par organisation, et une file Temporal par organisation
    écartée par décision** (2026-09-29, [ADR 0027](../adr/0027-le-voisinage-se-regle-a-l-admission-et-au-quota.md)).

    Le `ResourceQuota` des namespaces d'un projet était figé (32 CPU, 96 Gi, 40 pods). Un greffon le
    fixe désormais selon l'organisation (`choregos_core.quotas`), lu par l'étape de provisioning qui
    écrit dans le dépôt GitOps. Avec l'admission (plafonds de runs simultanés et mensuel, 0.9), une
    organisation ne dépasse ni ses runs, ni son budget, ni son quota. La file Temporal et le pool
    PostgreSQL par organisation sont écartés : ce qui consomme, ce sont les runs, pas l'orchestration.

    **Ce que ça prouve** : `test_quotas.py` — un greffon installé fixe le quota de SON organisation
    dans les manifestes écrits, une autre garde le défaut, sans greffon le défaut historique ; un
    quota mal écrit est refusé avant Argo CD. Lecture du quota retirée du provisioning : rouge.
    **Ce que ça ne prouve pas** : le quota prend effet au prochain provisioning ; rien ne réécrit
    ceux des projets existants.

27. **La transaction d'une requête est validée avant l'envoi de la réponse** (2026-10-04, #161).

    FastAPI ferme par défaut les dépendances `yield` APRÈS l'envoi de la réponse ; `get_db` y validait
    sa transaction. Mesuré sur une pile servie par uvicorn (essai du socle, élément 9) : juste après
    une connexion de développement d'un utilisateur neuf, la requête suivante répondait 401 « session
    périmée » six fois sur dix, l'utilisateur n'étant pas encore écrit. Plus grave : un `commit` qui
    échoue après coup laissait au client un succès sur une écriture perdue. `Db` et `DbPlateforme`
    passent en `Depends(..., scope="function")` : la session se ferme avant l'envoi.

    **Ce que ça prouve** : `test_commit_avant_la_reponse.py` appelle l'application ASGI directement —
    `ASGITransport` attend la fin de l'application et ne voyait rien — et exige que le `commit`
    précède `http.response.start` ; rouge sans le correctif. Suite entière verte, y compris la suite
    de l'API sur PostgreSQL.
    **Ce que ça ne prouve pas** : le comportement derrière un proxy qui bufferise les réponses (il
    masquerait la course, pas l'écriture perdue).

28. **L'heure d'authentification est celle de l'IdP, et `reauth=1` l'exige récente** (2026-10-04,
    #153).

    `Principal.authentifie_le` lisait l'`iat` de la session : l'heure où Choregos l'avait ouverte.
    Après une reconnexion SSO silencieuse — l'IdP a encore une session et rend la main sans rien
    demander — cette heure est neuve alors que l'utilisateur ne s'est pas authentifié depuis
    longtemps. Toute porte « authentification récente » était donc satisfaite par un aller-retour
    vers `/auth/login`, y compris le contrôle de fraîcheur de l'édition entreprise. La session porte
    désormais l'`auth_time` de l'ID token (lu sans vérifier sa signature : il vient du point `token`
    en TLS, OIDC Core §3.1.3.7) ; après `?reauth=1`, un `auth_time` absent ou antérieur à la poignée
    de main est refusé (401) ; `authentifie_le` lit `auth_time`, et `iat` seulement à défaut.

    **Ce que ça prouve** : `test_authentification_fraiche.py` — une reconnexion SSO garde l'heure de
    l'IdP ; un `reauth` que l'IdP n'a pas honoré, ou sans `auth_time`, est refusé ; une
    ré-authentification fraîche ouvre une session qui le dit ; sans `auth_time`, l'ancien
    comportement. Gardes retirées : trois rouges.
    **Ce que ça ne prouve pas** : une session SAML (édition entreprise) porte encore `iat` seulement ;
    il faudrait y passer l'`AuthnInstant`.

29. **Les outils d'un greffon arrivent chez l'agent ; les migrations du cœur ne décrivent que ses
    tables** (2026-10-04, essai du socle).

    Un greffon pouvait servir des routes (`declarer_un_routeur`) mais aucun outil n'arrivait chez
    l'agent : le catalogue ne se lisait que dans un fichier du déploiement. La couture
    `declarer_un_fournisseur_d_outils(nom, lister, appeler)` les fait passer par le chemin du
    catalogue : annoncés par `GET /internal/runs/{id}/tools` au serveur MCP `choregos-tools`,
    appelés par `POST /internal/runs/{id}/tools/{nom}` sous le jeton du run, sous le même plafond,
    avec une ligne au registre des coûts (`greffon:<nom>`) et l'événement `tool.called`. Un nom déjà
    pris est refusé en 409, jamais masqué. Et `db.models.objet_du_coeur`, filtre `include_object`
    d'Alembic : un greffon inscrit ses modèles dans le `Base` du cœur ; sans ce filtre,
    `test_migrations.py` rougissait dès qu'un greffon avait été importé plus tôt dans la session de
    tests, et une autogénération aurait fait entrer ses tables dans une migration du cœur.

    **Ce que ça prouve** : `test_greffons_outils.py` (annonce, appel compté, 401 sans jeton, 409 sur
    collision, rouge sans la garde) ; sans le filtre, `test_migrations.py` rougit après les tests
    d'un greffon.
    **Ce que ça ne prouve pas** : le prix d'un outil de greffon (0 € au registre, à trancher).

30. **L'adaptateur SCM écrit des fichiers sur une branche** (2026-10-04, essai du socle).

    Pour que la PLATEFORME ouvre une PR avec ses changements, jeton gardé — l'effet
    `gitops.pull_request` du futur socle. `ScmAdapter` savait créer une branche et ouvrir une PR, pas
    écrire un fichier. `commit_files(repo, branch, files, message)` : GitHub par l'API Contents (un
    commit par fichier modifié), un fichier identique n'est pas réécrit ; le faux fait de même, et
    son `open_pr` réutilise une PR ouverte sur la même branche, comme GitHub.

    **Ce que ça prouve** : tests d'adaptateur — création, mise à jour avec le `sha`, fichier
    identique sauté, panne en lecture sans écriture ; idempotence du faux.
    **Ce que ça ne prouve pas** : un commit unique pour plusieurs fichiers (API Git Data), ni un
    essai contre un vrai dépôt.

31. **`global.extraEnv` : un réglage que le chart ne connaît pas encore, sans fork du chart**
    (2026-10-04).

    Pour activer, par exemple, un greffon livré inactif sur un seul environnement. La variable arrive
    par `choregos.commonEnv` à l'API, à chaque worker et au Job de migration — un greffon activé dans
    l'API mais pas dans les migrations démarrerait sans ses tables. Une variable déjà posée par le
    chart est refusée au rendu : Kubernetes garderait la dernière des deux sans rien dire.

    **Ce que ça prouve** : `tests/charts/test_variables_supplementaires.py` — la variable arrive à
    chaque processus qui lit les réglages, Job de migration compris ; rien sans valeur ; un nom déjà
    posé (`CHOREGOS_DATABASE_URL`) est refusé, rouge sans la garde.
    **Ce que ça ne prouve pas** : la garde ne connaît que les variables de `commonEnv` ; une variable
    posée par un seul gabarit (l'OIDC de l'API) peut encore être doublée.

32. **La pile de développement démarre sur un poste neuf : RustFS remplace MinIO** (2026-10-04, #152).

    L'image du serveur MinIO n'est plus publiée, ni sur Docker Hub ni sur quay.io : `dev/compose.yaml`
    ne démarrait que sur un poste qui l'avait en cache. RustFS (`rustfs/rustfs:1.0.1`, Apache-2.0) la
    remplace, mêmes identifiants et mêmes ports ; il délivre aussi des identifiants STS bornés par une
    politique de session (essai du socle, élément 7 : Lakekeeper, PyIceberg et DuckDB passent avec
    lui).

    **Ce que ça prouve** : `tests/dev/test_pile_de_developpement.py` — chaque image de la pile porte
    une étiquette explicite, et aucune n'est le serveur MinIO ; les deux rouges sur l'ancienne pile.
    **Ce que ça ne prouve pas** : la tenue de RustFS en charge et dans la durée ; les données d'un
    volume MinIO existant ne sont pas reprises (nouveau volume `s3data`).

33. **L'essai de la phase 0 du socle est livré, inactif par défaut** (2026-10-04, #151).

    L'essai prouve, sur le cœur de Choregos, la tranche fine qui porte le futur socle (cahier des
    charges de la Varga Foundation) : une ontologie en YAML validée et compilée, des constats
    synchronisés depuis un rapport observations NDJSON v1, lus par un agent au moyen des outils MCP
    générés avec son jeton de run, une action `gitops.pull_request` proposée par l'agent, validée par
    un humain ré-authentifié et ouverte par la plateforme, et une preuve par relance du collecteur.
    Chaque élément a son rapport dans `essai/*/RESULTATS.md`. Le paquet `choregos-ontology` porte le
    greffon, déclaré par ses points d'entrée et **livré inactif** : il ne s'active qu'avec
    `CHOREGOS_ESSAI_ONTOLOGIE=1` (`global.extraEnv`).

    **Ce que ça prouve** : `packages/ontology/tests` (109 tests, dont deux sur PostgreSQL), et les
    éléments 1 à 6 rejoués contre une pile intégrée servie par uvicorn (`essai/demarrage-local`).
    Inactif, le greffon ne sert aucune route, n'annonce aucun outil, et n'a aucune table (testé).
    **Ce que ça ne prouve pas** : un modèle qui choisit ces outils, la décision dans la console, une
    PR sur un vrai dépôt, la reprise sur panne (le moteur d'actions tourne dans la requête, pas dans
    Temporal). C'est l'objet des tests sur le locataire dev, et du rapport de décision du J0.

34. **La console du locataire dev répondait 404 depuis son installation : les Ingress s'annotent**
    (2026-10-05).

    Le contrôleur d'ingress du locataire dev (NGINX Inc.) refusait l'Ingress de la console : celui de
    l'API avait pris l'hôte, et ce contrôleur n'admet pas deux Ingress sur un même hôte (« All hosts
    are taken by other resources »). Toute page de la console rendait le 404 de nginx ; seule l'API
    (`/api`) répondait. Son remède est l'Ingress fusionnable (un maître, des minions), qui exige
    d'annoter les deux : `choregos-api.ingress.annotations` et `choregos-web.ingress.annotations`.

    **Ce que ça prouve** : `tests/charts/test_annotations_d_ingress.py` — les deux Ingress portent les
    annotations demandées, aucune sans valeur ; rouge sans le changement.
    **Ce que ça ne prouve pas** : l'Ingress maître est posé par le déploiement (`choregos-deploy`),
    pas par ce chart ; la console servie sur le locataire se constate après la montée.
