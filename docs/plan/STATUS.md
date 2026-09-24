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
| S5-01 | S5 | 🟡 | — | socle Next.js / React 19 / TS strict / Tailwind + composants transverses. **Pas d'i18n** : `next-intl` est déclaré et jamais importé, l'interface est 100 % française (P0-5) |
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
| S7-07 | S7 | ❌ | — | ServiceMonitor, alertes et 6 dashboards livrés — **mais aucune métrique n'est émise** : pas de `/metrics`, pas de `prometheus_client`, 16 séries `choregos_*` référencées et inexistantes. Un décor (P0-4) |
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

**Total** : 96 livrées, 6 partielles, 0 non commencée.

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
- **Une documentation anglaise** de déploiement et d'usage (`docs/en/`), dont les exemples YAML
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
   seul corrigées — `add_member`, webhooks, orchestrateur, train). Reste : RLS fail-closed
   testée sur PostgreSQL (P0-3b), secrets du chart, limiteur, garde-fous du runner (P0-3c).

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
