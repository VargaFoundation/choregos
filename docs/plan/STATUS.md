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
| S0-08 | S0 | 🟡 | — | ci.yml ciblé par chemins, nightly.yml, release.yml (cosign, chart OCI). **Pas de SBOM** malgré ce que disait cette ligne et `SECURITY.md` ; pas de Trivy sur les images de release ; scan nocturne non bloquant (état des lieux du 2026-09-24, P1-2). La porte `ci-ok` n'accepte que `success` et `skipped` : un job `abandoned` (aucun runner hébergé ne l'a pris, 2026-10-05) la laissait verte sans que les tests Python aient tourné — `tests/ci/test_porte_de_la_ci.py` joue son script contre chaque résultat |
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
| S15-02 | S15 | ✅ | — | porte MCP des clients externes, `/mcp` et `/mcp/projects/{org}:{slug}`, servie par l'API (ADR 0030) : protocole écrit à la main, partagé avec le side-car (`choregos_core.mcp`, 202 pour une notification) ; 7 outils adossés aux services de l'API REST (`creer_un_ticket`, `chronologie` extraits) ; un outil hors des droits n'est pas annoncé (-32602) ; aucun outil ne décide ; audit `mcp.call`, débit par jeton, 50 écritures par jour, troncature ; `GET /integrations` ; chemin `/mcp` dans l'Ingress et la HTTPRoute ; `docs/integrations.md`. `test_mcp_porte.py` (20 tests), `test_mcp_interop_sdk.py` (le client de référence du SDK `mcp`, en test seulement, initialise, liste et appelle à travers uvicorn), `tests/charts/test_porte_mcp_routee.py`, `packages/tools-mcp/tests/test_transport.py`. Ne prouve pas un vrai Claude Code contre le dev (S15-07), ni OAuth (S15-08) |
| S15-03 | S15 | ✅ | — | outils générés de l'ontologie servis par la porte d'un projet (`/mcp/projects/{org}:{slug}`), avec les droits de l'HUMAIN : nouvelle couture `declarer_des_outils_pour_les_humains` (lister, appeler, en attente) ; une action ne s'annonce que si l'un des rôles de la personne figure dans `permissions.propose`, et seulement à un jeton `mcp:write` ; la proposition porte `proposed_by = {user, email, via: mcp}` ; elle apparaît dans `list_pending_decisions` avec le lien de la console (`/p/{slug}/proposals/{id}`) et `can_decide` (rang et séparation des rôles) ; le budget d'écritures compte aussi les propositions ; les noms d'outils de la porte sont réservés (ONT025). `packages/ontology/tests/test_mcp_humain.py` (3 tests, rouges sans l'enregistrement du greffon). Ne prouve pas la page de décision (S15-04) |
| S15-04 | S15 | ✅ | — | décider une proposition d'action dans la console : onglet `proposals` du projet (en attente, ou toutes), page d'une proposition (justification, cible, paramètres, qui l'a proposée et par où, historique des décisions, effets et preuves), approuver ou rejeter — un rejet exige un motif, et la page dit que qui propose ne décide pas. Un 401 `step_up_required` repasse par l'IdP avec `reauth=1` puis revient à la page, au lieu d'envoyer vers `/login` (qui bouclait, la session étant vivante). `tests/api.test.ts` (deux tests de la redirection), un parcours Playwright, axe sur la page. Ne prouve pas la ré-authentification par le vrai Keycloak depuis la console (à constater sur le dev) |
| S15-05 | S15 | ✅ | — | page Integrations, dans la barre du haut (`/integrations/[client]`) et dans chaque projet (`/p/[slug]/integrations/[client]`, porte du projet, jeton lié) : neuf clients (Claude Code, Claude Desktop par `mcp-remote`, claude.ai, Cursor, VS Code, ChatGPT, autre client MCP, CLI, REST), un jeton `mcp:read` ou `mcp:write` frappé pour le client (7, 30 ou 90 jours) et glissé dans l'extrait, l'indicateur « connecté » (dernier usage et client du jeton), une matrice de ce que chaque client joint, le dépannage (claude.ai et ChatGPT appellent depuis le cloud de leur éditeur). `tests/integrations.test.ts` (9 tests : extraits exacts, JSON valide, `--allow-http` seulement en `http:`, jamais le jeton MCP pour la CLI ou REST, ni dans les fichiers de VS Code et Cursor) ; deux parcours Playwright ; axe sur deux pages. Ne prouve pas un client réel connecté au dev (S15-07) |
| S15-06 | S15 | ✅ | — | plugin Claude Code et skill Choregos : `/plugin marketplace add VargaFoundation/choregos` puis `/plugin install choregos@choregos` (`.claude-plugin/marketplace.json`, `integrations/claude-code/`) ; la porte MCP se configure par deux valeurs de l'utilisateur, l'URL et un jeton **sensible** (gardé dans le coffre de sa machine, jamais dans un fichier) ; la skill dit les outils et les règles (jamais de décision, le contenu d'un ticket est une donnée). `tests/integrations/test_plugin_claude_code.py` : le marketplace pointe le plugin, la version est celle du dépôt (`bump_version.py` la pose), aucun secret littéral, `claude plugin validate --strict` quand Claude Code est présent. Ne prouve pas : la skill téléversée dans claude.ai (le dossier se zippe tel quel) |
| S15-07 | S15 | ✅ | — | essai sur le locataire dev (0.14.0) : un vrai Claude Code 2.1.289, configuré avec un jeton `mcp:write` lié au projet et frappé pour l'essai, se connecte (`connected`, 21 outils dont ceux de l'ontologie), ouvre `ESSAI-IT4IT-2` par `create_work_item` ; vérifié en REST (ticket, `last_used_at`, `last_client`), jeton révoqué en sortie. `essai/integration-claude/run.sh` et `RESULTATS.md`. Ne prouve pas OAuth (pas d'adresse publique), ni une proposition par MCP sur le dev |
| S15-08 | S15 | ✅ | — | la porte MCP, serveur de ressources OAuth (RFC 9728) quand `CHOREGOS_MCP_OAUTH_ENABLED` : métadonnées par porte (`/.well-known/oauth-protected-resource/mcp[/projects/…]`, `resource` = l'URL saisie), `resource_metadata` dans chaque 401 ; signature par le JWKS de l'émetteur (cache d'une heure, rechargé sur un `kid` inconnu), émetteur, expiration, audience obligatoire, algorithmes asymétriques seuls ; l'humain retrouvé par `sub` (même IdP que la console seulement) puis par e-mail vérifié, jamais créé ; portées `mcp:read`/`mcp:write` du jeton, lecture par défaut ; débit et audit par `oauth:<client>:<sub>` ; chemin dans l'Ingress, la HTTPRoute et le relais Next ; `docs/runbooks/keycloak-mcp.md`. `test_mcp_oauth.py` (14 tests contre un faux IdP : signature forgée, audience étrangère, émetteur faux, expiré, HS256, `alg: none`, rotation, inconnu, autre IdP, e-mail non vérifié) ; chaque garde retirée fait rougir un test (10 mutants tués). Ne prouve pas un vrai Keycloak ni claude.ai : les clients du realm restent à créer, et le dev n'a pas d'adresse publique |
| S15-09 | S15 | ✅ | — | la page Integrations propose l'authentification unique quand la porte accepte les jetons de l'IdP : le déploiement nomme les clients que son IdP a enregistrés (`global.mcp.oauth` du chart — `enabled`, `issuer`, `audience`, `clients` — rendu en `CHOREGOS_MCP_OAUTH_*`, au lieu de variables posées à la main ; le chart refuse un nom posé deux fois avec `global.extraEnv`) ; `CHOREGOS_MCP_OAUTH_CLIENTS` (JSON, par client de la page) arrête l'API au démarrage sur un nom que la page ne connaît pas ou un port de redirection hors de 1024-65535 ; `GET /integrations` rend `oauth.enabled`, l'émetteur et les clients (contrat `Integrations.oauth.clients`) — il disait `enabled: false` quoi que dise le déploiement. La page montre d'abord la commande de Claude Code (`claude mcp add --client-id … --callback-port …`, aucun jeton), le jeton un clic plus loin ; pour claude.ai, l'URL et l'identifiant du client sur une adresse HTTPS seulement, jamais un secret ; le tableau des clients dit l'état réel. Le guide d'exploitation `keycloak-mcp.md` montrait un `choregos-api.env` que le chart n'a jamais lu : corrigé, avec `offline_access` (Keycloak refuse toute la demande sur une portée non rattachée au client). ADR 0030 amendée. `test_mcp_oauth.py` (2 cas : ce que la page reçoit, deux réglages refusés), `tests/charts/test_porte_mcp_oauth.py` (4 cas : éteinte par défaut, clients rendus, l'API lit ce que le chart écrit, un nom posé deux fois), vitest `integrations.test.ts` (6 cas) et `integrations-oauth.test.tsx` (2 cas : l'authentification unique d'abord, la bascule vers le jeton) ; 15 mutants tués. Ne prouve pas une connexion réelle de Claude Code au dev par Keycloak : les clients du realm attendent la PR infra (DiametralGroup/infra#878) et sa synchronisation |
| S16-01 | S16 | ✅ | — | un ticket est épinglé à la VERSION du workflow où il est né (ADR 0031) : `load_context` lit l'épingle et la pose quand elle manque (seul le corps de l'activité change ; `tests/replay` rejoue) ; le miroir, le commentaire de suivi et le rattrapage lisent le workflow du ticket ; `projects.default_workflow` nomme le défaut ; une seule version active par nom (index unique partiel) ; la migration reprend les projets et épingle les tickets existants sous `set_config` (RLS forcée) ; republier un ancien nom par l'alias ne rend plus 500. `apps/orchestrator/tests/test_epingle.py` (une v2 publiée pendant l'attente ne déplace pas le ticket ; une épingle d'un autre projet est ignorée), `apps/api/tests/test_ticket_epingle.py` (l'écran nomme le workflow du ticket, plus `default-simple`). Ne prouve pas encore deux workflows actifs ensemble (S16-02) |
| S16-02 | S16 | ✅ | — | plusieurs workflows par projet (ADR 0031) : `GET /projects/{id}/workflows` (un actif par nom, le défaut marqué, les tickets ouverts), `GET|PUT …/workflows/{name}` (422 si `metadata.name` diffère, 409 sur une `base_version` périmée), `POST …/{name}/deactivate` (refusé pour le défaut ou une cible de routage), `…/versions` et `…/versions/{v}/restore` (la version suivante, rien d'écrasé), `GET|PUT /projects/{id}/workflow-routing` ; `publier_workflow` est le seul chemin d'écriture (l'alias, le PUT par nom, la restauration), qui ne désactive que la version active du MÊME nom ; `workflow_defs.created_by`, `projects.workflow_routing`. `test_workflows_multiples.py` (6 tests). Ne prouve pas encore la naissance d'un ticket dans le bon workflow (S16-03) |
| S16-03 | S16 | ✅ | — | un ticket naît dans son workflow (ADR 0031) : `services.routage.nouveau_ticket` est le seul constructeur (console, porte MCP, webhook, rattrapage, finding promu, démo) — il choisit le workflow (champ `workflow` explicite, sinon la première règle de routage qui correspond aux étiquettes ou au type, sinon le défaut), pose son état initial et épingle sa version ; plus d'`inbox` en dur (#173) ; `WorkItemCreate.workflow` et `.labels` ; `WorkItemData.item_type` lu sur Jira (`issuetype`). `test_un_seul_constructeur_de_ticket.py` (garde AST), trois tests de naissance dans `test_workflows_multiples.py`. Ne prouve pas le routage contre un tracker réel |
| S16-04 | S16 | ✅ | — | des champs de ticket (ADR 0031) : un workflow déclare le JSON Schema de ses champs (`metadata.inputs`, contrôlé par le validateur : un schéma d'objet valide) ; `work_items.fields`, validés à la naissance par `nouveau_ticket` (champ requis absent, champ étranger : 422 avec le chemin), rendus dans la vue du ticket et dans le contexte du run (`get_ticket` de l'agent) ; la porte MCP les accepte (`fields`, `workflow`, `labels`). `test_champs_de_ticket.py` (4 tests). Ne prouve pas encore une transition à date (S20) |
| S16-05 | S16 | ✅ | — | `migrate` : l'API lit et vérifie la cible (définition du projet, état courant présent ou mappé — 422 ; ticket occupé, décision attendue ou run en cours — 409) et l'envoie dans `Control.workflow` ; l'interpréteur, sous `workflow.patched("migration-par-definition")`, remappe l'état, consigne `workitem.migrated` par l'activité `record_migration` (clé `<workflow>/<run>/<n>`, rejouable) et l'épingle suit ; un ticket garé se réveille pour migrer ; une migration devenue impossible consigne `workitem.migration_refused` et laisse le ticket en vie. `test_migration.py` (orchestrateur, 3), `test_migration_de_ticket.py` (API, 4) ; deux historiques archivés rejouent : `wi-migration-S16-05` (nouveau chemin, marqueur) et `wi-migration-ancien-code` (enregistré avec l'interpréteur d'avant : ticket garé, migration appliquée à l'événement suivant) ; 9 mutants tués, dont chaque `patched`. Le schéma des événements connaît enfin `workflow_failed` et `tool.called`. Ne prouve pas la migration d'un ticket qui attend une décision (refusée, 409) ni un ticket déjà tué par l'ancien `migrate` (à réinitialiser) |
| S16-06 | S16 | ✅ | — | CLI : `workflow list <projet>` (version active, défaut, tickets ouverts), `workflow push <projet> [fichier] [--base-version N]` (PUT par le `metadata.name` du YAML, 409 si une autre version l'a remplacée), `items create … --workflow --label --field cle=valeur` (valeur lue en JSON quand elle en est) ; `docs/cli.md` régénéré. `packages/cli/tests/test_workflows.py` (5 tests contre une API simulée). Ne prouve pas la CLI contre un vrai serveur |
| S16-07 | S16 | ✅ | — | un gabarit livre ses workflows (`defaults.workflows` : fichiers du gabarit ou `template:<nom>@<v>`), le défaut (`default_workflow`), le routage (`routing`, rangé comme le PUT) et sa politique (`policy: preset:<nom>`) ; `ensure_defaults` lit enfin le manifeste (`services/gabarits.py`, un gabarit publié en base l'emporte sur le disque) ; un chemin qui sort du dossier, un fichier absent, un workflow désigné mais non livré, une politique hors `preset:` : 422 et aucun projet ; `defaults.workflow` au singulier reste lu. `test_gabarit_plusieurs_workflows.py` (8 tests, dont une demande étiquetée `leaver` qui naît au départ) ; 5 mutants tués. Ne livre pas encore de gabarit RH (lot 6, `joiners-leavers`) |
| S16-08 | S16 | ✅ | — | la vue « processus » (ADR 0031) : `choregos_core.dsl.to_process` dit chaque transition en clair — qui agit (l'agent et son rôle, une personne de quel groupe et sous quel délai, la plateforme, le train), de quel état vers lequel, ce qui doit être produit, les garanties résumées, les reprises, le rejet, le délai ; `POST /workflows/validate` la rend (`process`), `choregos workflow show --process` l'imprime. `packages/core/tests/test_process.py` (chaque garantie connue a un résumé ; agent, sorties, garanties, reprises ; personne, délai, rejet), `apps/api/tests/test_vue_processus.py`. L'écran vient avec S16-09 |
| S16-09 | S16 | ✅ | — | onglet **workflows** de la console : une carte par workflow (version active, défaut, ce qui y est routé en clair, tickets ouverts, auteur), « new workflow » depuis un gabarit (renommé par `metadata.name`), une page par workflow en quatre vues — **processus** (chaque transition dite en clair : qui, à quelles conditions, reprises, délai), **carte**, **YAML** (publie la version suivante par nom, `base_version` lue : 409 dit qu'une autre l'a remplacée), **historique** ; la carte ne dessine plus une arête `default` par état d'agent — la légende nomme les couloirs et dit les défauts une fois ; l'ancienne `/workflow` mène à la liste ; l'accueil ne dit plus « ties a repository ». vitest (`workflow-graph.test.ts` : défauts non dessinés mais placés, légende, description, renommage), e2e `parcours` (cartes, processus, carte à deux arêtes) et `accessibilite` (six pages de plus, parcours clavier de la carte). Ne prouve pas la modification depuis la carte (S16-12) ni le diff et la restauration (S16-13) — **Repris le 06/10** (la carte du dev était illisible) : ses couleurs lisaient des variables (`rgb(var(--agent))`…) que la console ne définit plus depuis le design system de la fondation — états sans bord, flèches et légende invisibles ; elles viennent désormais des jetons `--varga-*`, et React Flow est habillé aux mêmes jetons. Deux états du même couloir et de la même colonne s'empilent (ils tombaient au même point) ; les couloirs sont des bandes nommées derrière la carte ; arêtes à angles droits, de gauche à droite, avec leur pointe ; l'étiquette d'une arête nominale donne le nombre de garanties au-delà de 16 caractères, celle d'une arête secondaire ne se montre qu'au survol ; une carte trop longue pour tenir lisible s'ouvre à 0,8, sur son début. 6 cas vitest de plus (`workflow-graph.test.ts`), 7 mutants tués ; vérifié par capture d'écran sur le gabarit `default-simple` (11 états). **Et le même jour, une seconde passe** : les treize pointillés du gabarit (six reprises, six escalades, un rejet) croisaient encore toute la carte — le chemin nominal se lit désormais SEUL, et les arêtes secondaires d'un état se montrent quand on le survole ou qu'on le parcourt au clavier, toutes sur demande (une case au-dessus de la carte) ; une carte qui ne tient pas lisible s'ouvre avec une vue d'ensemble aux couleurs des couloirs, qui se glisse et se zoome — ce qui dépasse à droite se voit. 5 cas vitest de plus (`aretesAffichees` ; la couleur et les dimensions que lit la vue d'ensemble — sans elles, elle ne dessinait aucun état), 7 mutants tués ; e2e `parcours` (une escalade apparaît au survol de son état, disparaît, puis la case les montre toutes) ; vérifié par capture d'écran sur `default-simple`. Sur la même capture, chaque barre d'onglets finissait par les flèches ▲ ● ▼ de Windows : le pixel dont l'onglet actif descend sur le filet débordait en hauteur d'une barre qui défile en largeur — `Onglets` lui donne une marge basse, dans les quatre barres de la console ; vitest (la marge), e2e `accessibilite` (aucune barre ne déborde : scrollHeight = clientHeight), captures barres de défilement visibles, avant et après |
| S16-10 | S16 | ✅ | — | un board par workflow : sélecteur (`?workflow=` dans l'URL), colonnes lues dans le graphe du validateur — plus de regex sur le YAML, qui donnait zéro colonne à un YAML indenté de quatre espaces —, tickets épinglés au workflow (ceux d'avant l'épingle vont au défaut), un ticket dans un état inconnu reste visible ; « new request » choisit son workflow et en tire ses champs (`metadata.inputs` : date, choix, entier, booléen, liste, texte ; obligatoires marqués), envoyés typés avec `workflow` et `fields`. vitest `board.test.ts` (colonnes, filtre, champs, valeurs typées), e2e `parcours` (formulaire du flux d'incident, board par workflow). Ne prouve pas l'envoi contre un vrai serveur depuis la console (l'API valide, S16-04) |
| S16-11 | S16 | ✅ | — | `POST /workflows/edit` : douze opérations typées (états, transitions, garanties, acteurs) greffées dans le texte par `yaml.compose` (`choregos_core/dsl/edition.py`) — commentaires, guillemets, styles flow et bloc intacts ; un renommage suit chaque référence (initial, from/to, reprises, défauts) ; un élément en bloc emporte ses commentaires de tête ; la réponse porte le texte, le diff, la validation, le graphe, l'inverse et les avertissements (états à effet) ; rien n'est enregistré. `test_edition.py` (755 cas : chaque opération suivie de son inverse redonne les octets, sur un document mixte, sans fin de ligne, et sur les trois gabarits livrés ; refus motivés), `test_edition_de_workflow.py` (API) ; 7 mutants tués. Ne prouve pas l'édition depuis la carte (S16-12) ; le premier champ d'une transition écrite en bloc ne se retire pas (il partage la ligne du tiret) |
| S16-12 | S16 | ✅ | — | modifier un workflow depuis la **carte** et la **vue processus** : un clic sur un état ou une transition (ou Entrée sur un état, ou « edit » sur une étape) ouvre son panneau — libellé, nom (chaque référence suit), nouvelle transition (vers un état existant, ou un nouvel état : les deux opérations partent ensemble et s'annulent ensemble), retrait ; acteur, garanties, délai (prérempli), retrait ; chaque geste est une opération typée envoyée à `POST /workflows/edit` (S16-11), jamais une réécriture du YAML ; le brouillon est commun aux deux vues (il vit dans la mise en page du workflow), montre le dernier diff, annule geste par geste en rejouant l'inverse, et publie avec la `base_version` lue (409 dit qu'une autre version est passée, le brouillon reste) ; un geste avant la lecture ne part pas sur un texte vide. Un fichier partagé `apps/web/tests/fixtures/gestes-de-la-console.json` tient le contrat des deux côtés : vitest (`workflow-edition.test.tsx`, 23 cas : chaque geste émet son opération, chaque panneau émet la bonne, annuler rejoue le DERNIER inverse, 409) et pytest (`test_edition.py` : chaque geste de la console se greffe et se défait dans le cœur) ; e2e `parcours` (carte → panneau → diff → vue processus → publier v2) et `accessibilite` (carte en cours d'édition, Entrée ouvre le panneau) ; 8 mutants tués. Ne prouve pas l'édition des acteurs depuis la console (l'API la sait, aucun panneau ne la propose) ; les flèches secondaires (reprise, rejet) se modifient dans le YAML |
| S16-13 | S16 | ✅ | — | l'historique d'un workflow compare deux versions ligne à ligne (par défaut la précédente contre l'active ; `lib/diff.ts`, plus longue sous-suite commune, morceaux avec deux lignes de contexte, lecteur d'écran : « added » / « removed ») et restaure une version passée, après confirmation, en la republiant comme la suivante (`POST …/versions/{v}/restore`) : l'historique s'allonge, rien n'est réécrit. vitest `diff.test.ts` (relire le diff redonne chaque version), e2e `parcours` (comparaison et restauration). Ne prouve pas la restauration contre un vrai serveur depuis la console (l'API l'est, S16-02) |
| S16-14 | S16 | ✅ | — | ce qu'un workflow fait s'écrit (ADR 0037, issue #175) : `does: open_pr` (ou `merge_pr`) sur une transition système, `production: true` sur un état (contrat `workflow.schema.json`, `Transition.does`, `State.production`) — la PR, la fusion et le verrou de la production tenaient au NOM de l'état visé (`pr_*`, `merged*`, `deployed_prod*`), et un renommage depuis la console les changeait sans le dire. Les noms se lisent encore (`effet_de_la_transition`, `est_un_etat_de_production`, aides partagées par le validateur, l'interpréteur, les exigences de connecteurs et la vue processus) : un workflow publié garde son comportement, et l'interpréteur décide de la même définition — les DIX historiques Temporal rejouent à l'identique, sans `patched`. Le validateur avertit d'un effet déduit (`workflow.effet_implicite`) et refuse `does` hors d'un acteur `system`. Un renommage d'un état à effet ÉCRIT d'abord l'effet (`set_transition does`, `set_state production`), puis renomme — l'inverse défait le tout à l'octet près ; une transition sans `id` rend le renommage impossible plutôt que silencieux. Les trois gabarits du cœur écrits par les éditions typées elles-mêmes (11 lignes). La vue processus le dit (« the platform, which opens the pull request »), les exigences de connecteurs lisent l'effet (`scm` pour une transition qui ouvre ou fusionne, `cd` pour un état de production). `test_edition.py` (7 cas : le renommage écrit l'effet et s'inverse à l'octet, la production renommée garde son verrou, un effet écrit ne s'annonce plus, `does` refusé hors du système, une transition sans `id`, le validateur sait encore que la PR s'ouvre, un nom à effet donné s'annonce), `test_effets_ecrits.py` (Temporal : `pr_open` renommé `revue` et `merged` renommé `fusionnee` par l'édition typée — la PR s'ouvre, puis part en file de fusion), `test_process.py` et `test_exigences.py` (la vue processus le dit ; un état renommé exige encore `scm` et `cd`), `test_edition_de_workflow.py` (l'API écrit l'effet avant de renommer) ; l'exemple du contrat écrit ses effets ; 15 mutants tués (l'interpréteur, le validateur, les éditions, les exigences, la vue processus, les aides du contrat). Ne prouve pas un projet du dev renommé depuis la console (la release suivante) |
| S17-01 | S17 | ✅ | — | la couture « sections d'administration » (ADR 0032) : `declarer_une_section_d_administration(manifeste)` dans `greffons.py`, contrat `ui-manifest.schema.json` (blocs `form`, `table`, `action`, `secret_once`, avec un exemple SCIM) ; au démarrage, un manifeste invalide, une permission inconnue, ou un chemin qu'aucune route ne sert avec cette méthode arrête l'API ; `GET /api/v1/ui/admin-sections` ne rend que les sections dont l'appelant a la permission (`platform:admin` : l'administrateur de la plateforme seul). `test_sections_d_administration.py` (5 tests : visibles pour l'administrateur, pas pour un développeur ; quatre refus au démarrage). Ne prouve pas le rendu dans la console (S17-02) ni une section de l'EE (S17-04) |
| S17-02 | S17 | ✅ | — | `/admin` en sous-pages — vue d'ensemble (session, jetons), membres (inviter, changer un rôle, retirer après confirmation ; `DELETE /orgs/{org}/members/{user_id}`, audité, le dernier administrateur ne se retire pas : 409), audit (filtres, pages, export CSV aux formules désarmées), plateforme, édition (en communautaire, ce que l'EE ajoute, sans faux écran) — et une page par section qu'un greffon déclare, `/admin/x/{section}` : formulaire tiré du JSON Schema (`components/schema-form.tsx`, fait maison), table et actions de ligne, action confirmée, secret montré une fois ; aucun code de greffon dans la console. vitest `admin.test.tsx` (formulaire typé, chemins, CSV), `test_retirer_un_membre.py` (4), e2e `parcours` (section SCIM, secret une fois, édition) et `accessibilite` (cinq pages de plus). Ne prouve pas une section de l'EE réelle (S17-04) |
| S17-03 | S17 | ✅ | — | `image.registry` par composant (`choregos-api`, `choregos-orchestrator`), le job de migration suit l'API, et le digest global ne désigne pas une image d'un autre registre (#204). `tests/charts/test_image_par_composant.py` (3 tests). Ne prouve pas un déploiement réel de l'EE (S17-04) |
| S17-04 | S17 | 🟡 | — | l'édition entreprise sur le cœur courant, ses sections, servie au dev par un projet Harbor privé. **Côté cœur** : une section redéclarée par le même greffon passe (l'API charge les greffons deux fois — par l'orchestrateur qu'elle importe, puis dans `create_app()` — et refusait le doublon : avec l'EE, elle ne démarrait pas) ; une autre sous le même nom reste refusée ; une section qui demande `platform:admin` ne se montre qu'à la plateforme, quelle que soit sa portée (`org_admin` a toutes les permissions dans SON organisation) ; le formulaire sait les textes sur plusieurs lignes (`format: multiline`, un certificat PEM) et les correspondances (`object` de chaînes, `clé = valeur` par ligne ; une ligne illisible est dite, jamais effacée). `test_sections_d_administration.py` (2 cas), `admin.test.tsx` (4 cas) ; 5 mutants tués. **Côté EE** (dépôt privé) : plage de cœur 0.15, quatre sections (organisations, administrateurs de plateforme, limites, identité) et les `GET` qu'elles lisent. Ne prouve pas l'EE sur le dev : images EE sur 0.15, projet Harbor privé (PR infra soumise à revue) et valeurs de `choregos-deploy` restent à faire |
| S17-05 | S17 | ✅ | — | renommer une organisation, `PATCH /orgs/{org}` (contrat `updateOrg`, `OrgUpdate`) : son nom par son administrateur (`org_admin` — un propriétaire de projet a `member:manage`, pas ce droit), son slug par l'administrateur de la PLATEFORME seul, parce qu'il est dans chaque URL, chaque identifiant qualifié, chaque adresse de la porte MCP et le nom des groupes de l'IdP ; un slug pris : 409 ; audit `org.update` avec l'avant et l'après. Projets, appartenances et jetons suivent : ils tiennent à l'organisation par son identifiant. La RLS compare les SLUGS : le renommage se fait sous une portée d'instance, posée après le droit établi — bornée à l'ancien slug, la transaction ne verrait plus la ligne qu'elle renomme. Né d'une demande du 06/10 : le dev tournait dans une organisation au nom de l'hébergeur, et l'édition communautaire, qui n'en tient qu'une, ne savait ni la renommer ni en créer une autre. `test_renommer_une_organisation.py` (6 cas, verts aussi sur PostgreSQL : le slug change et les projets suivent, le nom seul, le slug comme geste d'instance, un slug pris, le propriétaire de projet refusé, un slug mal formé) ; 5 mutants tués, dont la portée d'instance sur PostgreSQL. Ne prouve pas le renommage sur le dev (la release suivante), ni d'écran : la route seule |
| S18-01 | S18 | ✅ | — | le registre d'agents de l'organisation (ADR 0033) : tables `agents`, `agent_versions`, `project_agents` sous RLS forcée ; un agent naît avec sa version 1, publier crée la suivante, aucune ne se réécrit, la révocation est définitive ; `agent:manage` (org_admin) crée, publie, suspend, révoque ; un projet épingle une version et ne peut que la resserrer (budget, limites, sous-ensemble des outils ; une surcharge qui élargit reçoit 422), la version effective est rendue. `test_registre_d_agents.py` (API), `test_rls_postgres.py` (un agent, ses versions et son épingle ne sortent pas de leur organisation). Ne prouve pas un agent dans un run (S18-02) |
| S18-02 | S18 | ✅ | — | un acteur nomme un agent (`AgentActor.agent: slug[@version]`, contrat) : `prepare_stage` résout la version effective (l'épinglée du projet, sinon la dernière, surcharges appliquées), qui fixe modèle et backend et ne fait que resserrer limites et budget ; les instructions, gabarit Jinja rendu en bac à sable (`choregos_core.instructions`), remplacent le playbook, cadrées par le contrat de sortie et les invariants (`cadrer`) ; une évasion est refusée à la publication (422) et au run ; le run nomme son agent (`runs.agent_slug`, `agent_version`) ; un agent révoqué, suspendu, expiré ou inconnu ne part pas, sans être réessayé ; les historiques archivés rejouent. `test_agent_du_registre.py` (dont un run complet de l'interpréteur), `test_registre_d_agents.py`. Ne prouve pas un vrai agent sur le dev |
| S18-03 | S18 | ✅ | — | `GET /orgs/{org}/agents/{slug}/metrics?days=30` : runs de l'agent, issue, taux de réussite (sur les runs terminés), coût par sorte — un appel d'outil compte — et par projet, dépense du jour ; un agent qui a dépensé son `daily_usd` depuis minuit (UTC), modèles et outils, ne lance plus de run (admission, sans reprise). `test_registre_d_agents.py` (un run échoué fait baisser le taux, l'outil compte), `test_agent_du_registre.py` (budget du jour). Ne prouve pas les pages Agents (S18-07) |
| S18-04 | S18 | ✅ | — | la bibliothèque de skills (ADR 0033) : tables `skills`, `skill_versions` sous RLS forcée ; une skill est un dossier dont le SKILL.md porte un `name` (celui de la skill) et une `description` ; `allowed-tools` refusé (une skill ne déclare aucune permission) ; l'import zip refuse le zip-slip, les liens symboliques, plus de 64 fichiers ou 512 Kio, un fichier non UTF-8, et retire un dossier racine commun ; versions immuables, désignées par une empreinte ; une skill dit quels agents la portent. `test_bibliotheque_de_skills.py`, `test_rls_postgres.py`. Ne prouve pas une skill posée dans un run (S18-05) |
| S18-05 | S18 | ✅ | — | `StageInput.skills` (nom, version, empreinte ; contrat) résolues par `prepare_stage` depuis la version de l'agent ; `GET /internal/runs/{id}/skills` (jeton du run seul) ; le runner recalcule l'empreinte (calcul partagé `empreinte_de_skill`) et s'arrête sur une skill altérée ou manquante (sortie 50), puis la pose là où le backend la lit (`.claude/skills`, `.agents/skills`, `.gemini/skills`, `.goose/skills`, `.opencode/skills`, `.github/skills`) — sinon `.choregos/skills` et un index dans le prompt —, hors du diff ; les cinq backends secondaires fusionnent enfin `launch.files`. `packages/runner/tests/test_skills.py` (18), API et orchestrateur. Ne prouve pas une skill lue par un vrai agent sur le dev |
| S18-06 | S18 | ✅ | — | les agents externes : `agent_credentials` (RLS forcée) rattache un jeton `mcp:*` ou un client OAuth (`azp`) à un agent `external` ; à chaque appel de la porte MCP le client incarne son agent — l'audit le nomme, ses outils sont ceux de l'humain intersectés avec ce que la version nomme (`mcp_servers` d'un connecteur `choregos`), un agent révoqué, suspendu ou expiré reçoit 401 dès l'appel suivant ; un humain sans droit donne un agent sans droit. `test_agents_externes.py` (9). Ne prouve pas les pages Agents (S18-07) |
| S18-07 | S18 | ✅ | — | les pages **Agents** de la console : `/agents` (barre du haut) — le registre (interne, externe, statut, version et son résumé) et **vos clients MCP** : vos jetons `mcp:*`, leur dernier appel et le client qui l'a fait ; un Claude Code qui a appelé la porte se dit *connecté*, « register as an external agent » crée l'agent (outils de son humain, ou lecture seule) PUIS rattache le jeton ; `/agents/{slug}` — 30 jours (runs, taux de réussite, coût par sorte et par projet, la journée contre le budget), versions immuables (en voir une, en publier une autre depuis la dernière), suspendre, réactiver, révoquer (confirmé), et pour un externe les clients qui l'incarnent (rattacher un de ses jetons, détacher) ; `/skills` — la bibliothèque (import zip, fichiers de chaque version, qui la porte) ; l'onglet **agents** d'un projet — ce qu'il épingle (resserré ou non) et ses **agents implicites** (acteurs `agent` qui ne nomment aucun agent du registre), à enregistrer. Contrat : `AgentCredential` porte `token_name`, `last_used_at`, `last_client`, lus du jeton. `test_agents_externes.py` (le dernier appel et son client, rien d'inventé avant), vitest `agents.test.tsx` (7 cas : implicites, connecté seulement s'il a appelé et plus une fois révoqué, lecture seule sans écriture, l'agent créé PUIS le jeton rattaché), e2e `parcours` (registre, Claude Code connecté, mesures, skill, agents implicites) et `accessibilite` (six pages) ; 5 mutants tués. Ne prouve pas l'enregistrement d'un agent implicite DANS le workflow (le nommer reste un geste de l'auteur), ni l'édition des skills et des serveurs MCP d'une version depuis la console |
| S19-01 | S19 | ✅ | — | les sortes de connecteurs s'ouvrent (ADR 0034) : un type se déclare au registre (`ConnectorTypeSpec` : nom, **capacités**, schéma de configuration, **champs secrets**, ancien nom) — `GET /connectors/types` le LIT (jira y était « indisponible » alors qu'enregistré ; un greffon y apparaît ; `pgvector` ne s'y propose plus, et un réenregistrement sans déclaration ne l'efface plus) ; `GET /projects/{id}/requirements` déduit des workflows actifs ce qu'ils exigent, et pourquoi (une garantie dit ce qu'elle lit : `ci_green` la CI, `scope_respected` un diff ; un rôle qui travaille dans un dépôt exige un `scm` ; un train ou un `deployed_prod*` un `cd`) — un projet RH n'affiche ni scm, ni ci, ni cd ; un secret ne s'écrit plus dans `config` (422) mais en **référence** champ par champ (`secret_refs`, migration `c5d7e9f1a3b6`), résolue par la couture `SecretResolver` (`env:` dans le cœur, `declarer_un_resolveur` pour un coffre) quand l'adaptateur est construit — `secret_ref`, stocké et jamais lu depuis le schéma initial, vaut pour le premier champ secret ; la console tire le formulaire du schéma du type et ne montre que ce qui est exigé ou configuré (« add a connector » pour le reste). Contrat : `ConnectorType.capabilities|secret_fields`, `Connector.secret_refs`, `ProjectRequirement`, `kind` ouvert. `test_exigences.py`, `test_secrets.py`, `test_types_de_connecteurs.py`, `test_connecteurs_par_capacites.py` (l'adaptateur testé reçoit la valeur, la réponse ne la dit pas), `test_installation_neuve.py` corrigé (un tracker `github` n'existe pas, l'API l'acceptait), vitest `connecteurs.test.ts`, e2e `parcours` et `accessibilite` ; 5 mutants tués. Ne prouve pas plusieurs connecteurs par capacité ni les instances d'organisation (S19-02), ni le type `mcp` (S19-03) |
| S19-02 | S19 | ✅ | — | les connecteurs de l'ORGANISATION et la politique de chaque opération (ADR 0034) : tables `org_connectors`, `connector_operations`, `project_operation_policies` sous RLS forcée (migration `d6e8f0a2b4c7`) ; une instance (`POST /orgs/{org}/connectors`, secrets en références) naît avec les opérations que son type déclare (`OperationSpec` : une lecture `allowed`, une écriture `approval`) ; l'administrateur de l'organisation seul (`tools:grant`) déclare, retire, et règle pour chaque opération sa politique (`allowed`/`approval`/`forbidden`), ses groupes de projets (aucun : tous) et son prix ; un projet voit ce que ses groupes ouvrent (`GET /projects/{id}/operations` : politique de l'organisation, la sienne, l'effective) et ne fait que RESSERRER (`approval` → `allowed` : 422 ; un durcissement ultérieur de l'organisation l'emporte toujours) ; console : Administration › connectors (opérations, politique, groupes), réglages du projet (le choix ne propose que la politique de l'organisation ou plus strict). `test_operations_par_politique.py` (5 cas, verts aussi sur PostgreSQL sous RLS : une autre organisation n'existe pas), vitest `operations.test.ts`, e2e `parcours` et `accessibilite` ; 5 mutants tués. Ne prouve pas qu'un run n'atteint que ce que sa politique permet (le courtier, S19-04) ni les opérations découvertes d'un serveur MCP (S19-03) ; aucun type livré n'expose encore d'opération (le type entra arrive en S20-03) |
| S19-03 | S19 | ✅ | — | le type de connecteur `mcp` (ADR 0034) : un client MCP écrit à la main (`choregos_adapters.mcp`) — `initialize` puis `notifications/initialized`, `Mcp-Session-Id` et `MCP-Protocol-Version` sur chaque requête, réponse JSON ou SSE (le message qui porte l'identifiant, rien d'autre), `tools/list` suivi de page en page (`nextCursor`, 50 pages au plus), `tools/call` ; le jeton est celui DU CONNECTEUR, en référence ; `FakeMcpServer` (transport en mémoire, scriptable, note chaque requête) ; `POST /orgs/{org}/connectors/{name}/discover` tire le diff : un outil NOUVEAU naît fermé (`forbidden`), un schéma d'entrée qui DÉRIVE (empreinte canonique) referme l'opération, un outil retiré disparaît, `readOnlyHint` fait une lecture et tout le reste une écriture ; une clé irrésoluble rend 502 en la nommant et laisse le connecteur en erreur ; console : « discover » et son diff. `test_client_mcp.py` (JSON et SSE, trois pages, session et jeton sur chaque requête, refus nommés, empreinte), `test_decouverte_mcp.py` (4 cas, verts aussi sur PostgreSQL), vitest, e2e ; 5 mutants tués. Ne prouve pas qu'un run atteint ces outils par la plateforme (le courtier, S19-04), ni l'OAuth client credentials vers un serveur (seul le jeton porteur) |
| S19-04 | S19 | ✅ | — | le courtier (ADR 0034) : `services/courtier.py` réunit, derrière `/internal`, le catalogue, les greffons et les opérations des connecteurs de l'organisation — un run voit `<connecteur>__<opération>` si la version de son agent la SÉLECTIONNE (`mcp_servers` et motifs ; un serveur nommé sans motif n'ouvre rien, tout s'écrit `*`), si sa politique effective est `allowed` (après ce que le projet resserre ; `approval` est une action gouvernée, pas un outil), et si ses groupes croisent ceux du projet ; l'appel passe par la plateforme : arguments vérifiés contre le schéma d'entrée gardé à la découverte (migration `e7f9a1b3c5d8`, 400 sans rien compter), plafond d'appels du run (429), clé DU CONNECTEUR résolue dans l'API — le pod ne tient que son jeton de run —, coût au registre (`provider: mcp:<connecteur>`, le prix de l'opération), événement `tool.called`, et ce que le serveur rend passé à la garde contre l'injection (`warn` le dit, `block` le retient : 451). `test_courtier.py` (8 cas, verts aussi sur PostgreSQL : sélection ∩ politique, interdit ou sous validation → 404 sans rien atteindre, la clé atteint le serveur et jamais le pod ni la réponse, argument hors schéma, plafond, injection, agent absent ou qui nomme un autre serveur) ; 5 mutants tués (un équivalent remplacé par un test plus fort). Ne prouve pas l'appel d'une opération `approval` comme action gouvernée (S20-01, S20-02), ni le side-car contre un vrai serveur (l'e2e du scénario RH, S20-07) |
| S19-05 | S19 | ✅ | — | une écriture appelée par un run est une action gouvernée, même permise (issue #241, amendement de l'ADR 0034, §7 ; écart relevé en écrivant le cahier v0.6, décision D6) : une LECTURE permise reste un appel direct ; une ÉCRITURE devient une action d'origine `tool`, approuvée par la politique (`by: policy`, aide `approuvee_par_la_politique` partagée avec les transitions), consignée sous sa clé, faite une fois, compensable, jouée par l'`ActionWorkflow` — jamais par la requête : la ligne validée AVANT le départ du workflow, la portée du jeton de run reposée ensuite. Le courtier attend son issue, borné (`courtier_attente_ecriture_s`, 30 s), par des lectures COURTES (une transaction tenue ouverte bloquerait le worker sous SQLite), et rend à l'agent ce que le serveur a répondu (`result.action` en plus) ; au-delà, 202 et l'identifiant ; un refus, 422 et l'action qui le consigne. Le même appel (run, outil, arguments) est la même action (identifiant uuid5) : rejoué, il n'écrit pas deux fois et son prix ne se recompte pas ; le résultat d'une écriture passe la garde contre l'injection comme une lecture. Contrat : la description de `callRunTool`. `test_courtier.py` (le cas de l'écriture : rien n'atteint le serveur depuis la requête, l'action née approuvée et démarrée, le même appel la même action), `test_connecteur_entra.py` mis au contrat, `tests/e2e/test_ecriture_gouvernee.py` (vrai Temporal de test, 3 cas : faite une fois et son résultat rendu, rejouée sans seconde écriture, la lecture directe ; le refus rendu à l'agent ; le prix compté une fois et la garde contre l'injection) ; 10 mutants tués, dont la validation avant le départ du workflow. Ne prouve pas une écriture jouée sur le dev (la release suivante), ni une écriture qu'un serveur met plus de 30 s à faire (202 : l'agent ne lit pas encore l'issue plus tard) |
| S20-01 | S20 | ✅ | — | les actions gouvernées entrent dans le cœur (ADR 0035) : tables `actions` et `action_effects` sous RLS forcée (migration `f8a0b2c4d6e9`) ; la couture `declarer_un_effet` et l'effet du cœur `connector.call` (une opération d'un connecteur de l'organisation, clé résolue, arguments vérifiés ; `approval` s'exécute — l'action est approuvée —, `forbidden` jamais ; un 4xx est un refus définitif, un 429/5xx est passager) ; `POST /projects/{id}/actions` propose (effets déclarés, sinon 422), `…/decision` décide : session humaine (un jeton d'API ou un client MCP ne décide pas), rang d'approbateur, séparation des rôles (qui propose — ou possède l'agent qui propose — ne décide pas), authentification fraîche (`fraicheur.py`, 401 `step_up_required` que la console suit), `min` approbateurs distincts, ligne relue verrouillée et passage à `approved` en compare-and-set ; l'approbation démarre `ActionWorkflow` (`action-<id>`) et répond AVANT tout effet ; chaque effet est consigné sous sa clé (`<action>:<n>`) AVANT d'être tenté et confirmé après — une clé faite ne se refait pas, une clé commencée (worker tué) se reprend ; une panne passagère est retentée, un refus arrête la suite et ce qui était fait est COMPENSÉ à rebours (l'action dit ce qu'elle n'a pas pu défaire) ; paramètres en Jinja isolé (`params`, `effects`, `result`) ; événements `choregos.action.*`. `test_actions_gouvernees.py` (8 cas, verts aussi sur PostgreSQL : rien ne s'exécute dans la requête, séparation des rôles, rejet motivé et 409, rang et jeton d'API, fraîcheur, deux approbateurs, deux approbations simultanées → un seul démarrage, effet inconnu), `test_actions.py` (Temporal : une panne passagère ne refait pas l'effet fait ; un refus compense à rebours ; une clé faite ne se refait pas, une commencée se reprend), historique `action-S20-01` archivé et rejoué ; 6 mutants tués. Ne prouve pas la migration de l'ontologie sur ces actions (`onto0003`, devenue S20-08), ni la boîte de décisions de la console (S20-02) |
| S20-02 | S20 | ✅ | — | un outil « avec validation » devient une action (ADR 0035) : le courtier annonce aux runs les opérations `approval` (« needs a human approval »), et les appeler n'atteint RIEN — une action gouvernée est proposée au nom de l'agent (`agent:<slug>`, origine `tool`, effet `connector.call`), l'appel rend son identifiant et le chemin de sa décision (202), et compte dans le plafond d'appels du run ; le propriétaire de l'agent ne l'approuve pas (séparation des rôles) ; la boîte des décisions : `GET /orgs/{org}/actions` (les projets que l'appelant voit), la page **approvals** (barre du haut), l'onglet **actions** d'un projet et la page d'une action (paramètres, effets et compensations, décisions — qui, quand, combien de minutes après s'être authentifié —, journal clé par clé), la décision dans la console (rejet motivé ; `step_up_required` repasse par l'IdP). Contrat : `listOrgActions`, `Action.project_slug`. `test_boite_des_decisions.py` (l'outil propose et rien n'atteint le serveur, même approuvée l'action n'est jouée que par Temporal, le propriétaire de l'agent refusé, une autre personne approuve ; une proposition compte dans le plafond), `test_actions.py` (Temporal : l'action approuvée atteint le serveur avec la clé du connecteur), `test_courtier.py` et `test_connecteur_entra.py` mis au contrat (une opération `approval` n'est plus un 404 : elle propose), vitest `decision.test.tsx`, e2e `parcours` et `accessibilite` (quatre pages) ; 3 mutants tués. La concurrence de deux approbations et la ré-authentification sont prouvées par S20-01. Ne prouve pas une action issue d'une étape de workflow (S20-05) |
| S20-03 | S20 | ✅ | — | le type de connecteur `entra` (Microsoft Graph, capacité `identity`) : jeton par client credentials (secret en référence), six opérations DÉCLARÉES avec leur schéma d'entrée (`get_user` en lecture, `allowed` ; `create_user`, `add_to_group`, `remove_from_group`, `disable_user`, `revoke_sessions` en écriture, `approval` — des actions gouvernées, pas des outils) ; chaque geste est IDEMPOTENT (un compte cherché par UPN avant d'être créé, « already exist » est un succès, un retrait absent aussi) ; l'UNITÉ ADMINISTRATIVE borne ce que la plateforme touche (un compte créé y entre, un geste hors d'elle est refusé, nommé, avant toute écriture) ; `Retry-After` respecté ; `FakeEntra` scriptable (comptes, groupes, unité, 429 à la demande) ; le courtier appelle aussi les connecteurs qui ne sont pas MCP (`executer`), au format d'un résultat MCP, et vérifie leurs arguments contre le schéma déclaré. `test_entra.py` (5 cas : double création, double ajout, hors de l'unité, départ, 429), `test_connecteur_entra.py` (3 cas : opérations déclarées, lecture par le courtier et écriture sous validation, hors de l'unité même ouvert) ; 4 mutants tués. Ne prouve pas l'appel d'une écriture comme action validée (S20-01, S20-02), ni le vrai Graph (un essai contre entra-mock reste à écrire) |
| S20-04 | S20 | ✅ | — | les faux du scénario RH : trois familles métier déclarées (`choregos_adapters.familles`, ADR 0034) — `mdm` (`enroll_device`, `wipe_device`, `device_status`), `shipping` (`create_shipment`, `create_return`, `shipment_status`), `access_control` (`activate_badge`, `deactivate_badge`, `badge_status`) —, chacune avec un type `demo` (en mémoire, clé `api_key` en référence, refusé en staging et en prod ; lectures `allowed`, écritures `approval`) ; leurs faux (`fakes/rh.py`) : écritures idempotentes, refus nommés de ce qui contredit l'état (un poste inscrit pour un autre, une référence prise par un autre envoi, un badge inconnu qu'on croit couper), clé vérifiée à chaque appel ; l'agent du fournisseur (`commander_poste`, `suivi_commande`) servi en MCP, comme chaque faux peut l'être sur le même état (`serveur_mcp`) ; `FakeMcpServer` sait FAIRE (`gestes`, rendus en `structuredContent`, un refus en `isError`), `FakeEntra` vérifie le secret de l'application. Suite de conformité commune `tests/conformance/connecteurs/` (28 cas, 8 implémentations : les trois familles typées puis servies en MCP, le fournisseur, Entra) : schémas fermés que l'implémentation suit (mêmes paramètres, mêmes types, requis exactement sans valeur par défaut), une écriture rejouée ne change rien, une lecture n'écrit pas (même d'un objet inconnu), la clé sert (une mauvaise : 401) et n'apparaît ni au journal (logging et structlog), ni dans un résultat, un refus ou un `repr` ; `test_faux_rh.py` (6 cas), `test_connecteurs_metier.py` (4 cas, verts aussi sur PostgreSQL : une famille se déclare clé en référence, une écriture approuvée atteint le parc avec la clé résolue, un refus est définitif) ; 11 mutants tués (un équivalent remplacé). Ne prouve pas un faux servi hors du processus : le type `demo` garde son état dans chaque processus, et l'API et l'orchestrateur du dev ne le partagent pas (le scénario sur le dev, S20-07) ; ni un vrai MDM, transporteur ou lecteur de badges |
| S20-05 | S20 | ✅ | — | une transition système propose une action gouvernée, à date (ADR 0035) : `Transition.action` (contrat `workflow.schema.json`, `TransitionAction` : `kind`, titre, justification et paramètres rendus en Jinja isolé avec les champs du ticket, effets et compensations, `approval` facultative, `not_before: fields.<date> ± Nd|h|m` — une date seule vaut minuit UTC) ; le validateur exige un acteur `system`, un champ déclaré comme date dans `metadata.inputs` et un `on_fail` (sans lui le ticket reproposerait la même action sans fin), et refuse `action_succeeded` sans action ; un effet inconnu est refusé à la PUBLICATION. L'interpréteur (sous `patched("actions-de-transition")`) attend la date, RECALCULÉE à chaque `fields_changed` (un ticket en pause n'agit pas), propose l'action une fois par tentative (identifiant tiré du ticket, de la transition et de la tentative : une activité rejouée la retrouve) et attend qu'elle soit RÉGLÉE — `action_settled`, envoyé par l'`ActionWorkflow` à sa clôture et par l'API sur un rejet, avec une relecture en base toutes les six heures pour filet ; `action_succeeded` (implicite) juge, un rejet suit `on_reject`, un échec `on_fail`, une action impossible à proposer le dit. La politique décide quand chaque opération appelée — compensations comprises — est permise à ce projet et que le workflow n'exige pas de validation (l'action naît approuvée, `by: policy`) ; sinon une personne, comme toute action. La politique du PROJET compte enfin, son resserrement et ses groupes : une opération qu'il s'interdit est refusée à la proposition (422) et à l'exécution — `connector.call` ne lisait que celle de l'organisation. `PATCH /work-items/{id}` change les champs (validés par le workflow épinglé, `null` en retire un) et prévient l'interpréteur ; un effet de greffon déclare sa politique (`approval` par défaut) ; un gabarit qui sort du bac à sable est un refus, pas une panne. `test_action_a_date.py` (Temporal en temps accéléré, 6 cas : rien avant J-10 puis l'action, apprise par `action_settled` ; la date déplacée réarme, avancée dans le passé fait partir sans attendre ; en pause rien ne part ; rejetée rien n'est joué ; impossible à proposer, `on_fail` ; un signal perdu, la relecture), historique `wi-action-a-date-S20-05` archivé, les neuf historiques rejouent ; `test_action_de_transition.py` (API, 7 cas, verts aussi sur PostgreSQL), `packages/core/tests/test_action_de_transition.py` (20 cas), exemple de contrat `workflow.onboarding-actions.json` validé par le schéma et le modèle ; 28 mutants tués. Ne prouve pas un champ changé depuis la console ou la porte MCP (l'API seulement), ni le scénario RH de bout en bout (S20-07) |
| S20-06 | S20 | ✅ | — | la tâche humaine : un formulaire, une preuve. Contrat : `Transition.task` (`TaskSpec` : titre, instructions, formulaire en JSON Schema d'objet, phrase à attester), `HumanRequestKind.TASK`, `HumanDecision.values` et `attestation`, `DecisionRequest` `complete` (`values`, `attested`). Le validateur exige un acteur `human`, un formulaire d'objet non vide, et chaque propriété déclarée dans `metadata.inputs` ; la vue processus la dit. L'interpréteur (sous `patched("taches-humaines")`) demande une tâche (`kind: task`, formulaire et attestation dans la demande) et verse les valeurs reçues dans les champs qu'il tient — une date saisie arme l'action qui l'attend. `complete` n'accepte que ce que le formulaire décrit (hors formulaire, champ requis absent ou mal formé : 422 avec son chemin), exige l'attestation quand la tâche en demande une, puis écrit les valeurs dans les champs du ticket, revalidés par `metadata.inputs` ; une tâche ne s'approuve pas, une approbation ne se complète pas, une tâche impossible se renvoie avec sa raison. La décision garde les valeurs et la phrase attestée telle qu'elle a été montrée ; la chronologie la dit : la preuve. Console : la barre de décision d'une tâche montre les instructions, le formulaire (`SchemaForm`) et l'attestation — « done » fermé tant qu'un champ requis manque ou que rien n'est attesté ; le board renvoie à la page du ticket. `action_settled` part désormais vers l'interpréteur que la proposition a consigné (`proposed_by.workflow_id`), et non plus vers celui que l'on déduit du ticket. `test_tache_humaine.py` (Temporal : l'UID saisi arrive dans les paramètres d'`activate_badge`, que les lecteurs de démonstration de S20-04 reçoivent avec la clé du connecteur, `action_settled` à l'interpréteur qui l'a proposée ; une date saisie arme l'action sans avancer l'horloge), historique `wi-tache-badge-S20-06` archivé, les dix historiques rejouent ; API (3 cas, verts aussi sur PostgreSQL), cœur (5 cas), vitest `tache.test.tsx` (3 cas) ; 21 mutants tués (un survivant de la console tué par un test plus fort). Ne prouve pas la tâche faite depuis la porte MCP ni par un agent (une tâche est humaine), ni le scénario RH de bout en bout (S20-07) |
| S20-07 | S20 | ✅ | — | le gabarit `joiners-leavers`, le scénario RH de bout en bout, et ce qu'il faut pour le jouer sur le dev : deux workflows sans dépôt ni CI — l'arrivée (plan d'accès de l'agent validé par les RH ; compte et groupe à J-10 ; groupe sensible sur validation ré-authentifiée, rejet sans arrêt ; poste commandé à l'agent du fournisseur en MCP, inscrit au parc sous le numéro qu'il attribue et expédié, à J-7 ; badge remis — une tâche attestée — puis activé ; contrôle à J+1) et le départ (à J0 compte, sessions et badge coupés d'un geste ; poste repris, reçu — une tâche —, effacé ; contrôle) ; routage `offboarding`/`depart` ; deux agents (`coordinateur-onboarding`, `coordinateur-offboarding`) et deux skills (`procedure-onboarding`, `profils-d-acces`) INSTALLÉS dans l'organisation à la naissance d'un projet (`defaults.agents`, `defaults.skills`), en version 1, jamais réécrits — `services/installation.py` ; les connecteurs de l'organisation que les workflows appellent se déclarent (`requires.org_connectors`) ; contrat `template.schema.json` (agents, skills, org_connectors, `steps` facultatives). Deux effets du cœur : `verifier` (un contrôle, `allowed`) et `connector.call`, dont un outil MCP rend ce qu'il a STRUCTURÉ (`effects[0].serial`). Un acteur qui nomme un agent du registre n'avertit plus d'un playbook absent. Conformité des gabarits étendue (25 cas) : plusieurs workflows sur fichier, validés sans avertissement ; défaut et routage ; chaque `connector.call` nomme un connecteur annoncé, une opération que sa capacité déclare, ses arguments requis et rien d'autre ; seuls des effets du cœur ; agents et skills installables, skills nommées livrées, agents nommés livrés. `test_gabarit_joiners_leavers.py` (5 cas : naissance, seconde installation qui ne réécrit rien, départ routé et champs exigés, lien symbolique refusé, `verifier` et résultat structuré) ; 16 mutants tués, dont 7 du gabarit lui-même (opération mal nommée, argument manquant ou en trop, connecteur non annoncé, routage, skill ou agent absents). Le scénario de bout en bout, `tests/e2e/test_m6_rh.py` (temps accéléré, sur les faux de S20-04, sans cluster) : l'arrivée — la demande déposée par le Claude d'une RH à la porte MCP (jeton `mcp:write`), le plan de l'agent installé par le gabarit, la validation RH, rien avant J-10 puis le compte et son groupe (la politique décide), le groupe sensible approuvé par une personne ré-authentifiée, les outils du fournisseur découverts FERMÉS puis la commande sous validation, le parc et l'envoi sous le numéro qu'il a attribué, la clé de chaque connecteur arrivée à son faux, le badge remis (tâche attestée) puis actif, le contrôle à J+1, cinq actions réussies et la preuve dans la chronologie ; le départ — rien avant J0, puis compte désactivé, sessions révoquées, badge coupé, reprise planifiée, réception attestée, poste effacé, contrôle. Retirer une brique le fait rougir : onze retraits essayés (la date J-10, la validation renforcée, le numéro de série, la date J0, la révocation des sessions, le démarrage d'une action approuvée, la politique qui décide, `action_settled`, les valeurs de la tâche, l'installation des agents, la clé du parc), onze rouges. Pour le dev, où l'API (deux répliques) et l'orchestrateur sont des processus distincts : les faux servis par UN processus, `choregos_adapters.fakes.serveur` (application ASGI sans cadriciel : MCP pour le parc, le transporteur, les lecteurs et le fournisseur, et un faux Microsoft Graph), qu'un connecteur `demo` joint quand il porte une `url` (`GuichetDistant` ; sous `CHOREGOS_FAKES` rien ne sort du processus) ; le chart les déploie (`demoFakes.enabled`, une réplique, la politique réseau ouverte à ce pod seul, jeton par référence) et les refuse en staging et en prod ; `essai/rh/scenario_rh.py` prépare, dépose par la porte MCP, suit, déplace une date, lance le départ et vérifie — sans rien décider : les décisions et la tâche restent humaines, dans la console. `test_faux_servis.py` (4 cas : deux processus voient le même parc, le faux Graph par son adresse et son secret, un jeton faux refusé, le choix du guichet), la suite de conformité étendue aux faux servis (37 cas), `tests/charts/test_faux_de_demo.py` (4 cas) ; 8 mutants tués (le choix du guichet, le préfixe du faux Graph, le jeton, le refus d'un outil, la garde, la règle réseau, la réplique unique). Joué sur le dev en 0.16.1 le 06/10 (`essai/rh/RESULTATS.md`) jusqu'à la première décision humaine : connecteurs de l'organisation vers le pod des faux, outils du fournisseur découverts, projet né avec ses workflows, agents, skills et registre, demande déposée par la porte MCP, plan de `coordinateur-onboarding` (opencode, skills chargées, l'annuaire lu par le courtier) — après un correctif du script : un projet neuf partait sur `claude-code`, que le dev ne sert pas. Ne prouve pas la suite sur le dev : les validations, la commande et la tâche attendent des personnes |
| S20-08 | S20 | ✅ | — | l'ontologie passe sur les actions du cœur. Le cœur sait attendre une preuve : un effet demande qu'on attende la preuve qu'il a servi (`attendre_une_preuve: {jusqu_a}`) ; l'`ActionWorkflow` (sous `patched("preuve-attendue")`) montre `awaiting_evidence` (nouvel `ActionStatus`, contrat), attend le signal `preuve` jusqu'à l'échéance — favorable, la suite reprend ; contraire ou absente, l'action échoue et ce qui était fait se COMPENSE à rebours ; la couture `signaler_une_preuve(action, position, ok, detail)` la remet ; `cloturer` fusionne le résultat au lieu de l'écraser (l'attente et les preuves y restent). `test_preuve_attendue.py` (Temporal, 3 cas : la preuve vient et l'action réussit, une preuve contraire compense, sans preuve avant l'échéance elle échoue), historique `action-preuve-S20-08` archivé, les onze rejouent ; la couture testée côté API ; 6 mutants tués. Puis l'ontologie : une proposition est une action du cœur (origine `ontology`, MÊME identifiant), proposée par `proposer` — mêmes règles qu'avant : cibles, préconditions CEL, chemins permis, politique, idempotence —, décidée par `decider` (ses approbateurs en rôles du cœur ; une règle sans `stepUp` n'exige pas d'authentification récente : `step_up_minutes` absent, ce que le cœur sait désormais lire), jouée par l'`ActionWorkflow` : chaque effet de l'ontologie est l'effet `ontology.effet`, chaque preuve l'effet `ontology.preuve` — `collector.rerun` fait attendre l'action jusqu'au rapport suivant, que `on_report` lui REMET par `signaler_une_preuve` ; un refus se consigne dans le dossier de l'action dans sa propre transaction. Les routes `/proposals` et les outils MCP restent, vues sur les actions du cœur ; la décision d'une attente MCP mène à la page des actions. `onto0003` copie `action_proposals` dans `actions` — ses effets du cœur déduits de la version, ses approbateurs traduits ; une proposition interrompue en vol close en `failed`, et dit pourquoi — sous `set_config` (RLS forcée), puis retire la table ; la descente la rend. Deux défauts du cœur trouvés en chemin : le WORKER ne chargeait pas les greffons — l'effet d'un greffon y était « inconnu » là même où il devait s'exécuter (`test_worker_greffons.py`) — et une action démarrée par la requête qui la décide pouvait être lue avant la validation de celle-ci (`charger_l_action` retente désormais une ligne pas encore visible). Tests de l'ontologie passés sur un vrai Temporal de test (23 cas des actions et de la porte MCP, 113 pour tout le greffon : la PR ouverte après la décision et jamais avant, la preuve par le rapport suivant, un rapport partiel, une couche absente ou injoignable, le délai dépassé dans Temporal, l'idempotence, la séparation des tâches, le jeton d'API qui ne décide pas), migration testée avec des lignes sur SQLite et sur PostgreSQL sous RLS forcée (rouge sans `set_config`) ; une règle sans `stepUp` décidée sans 401, une action lue avant sa validation retentée (tests du cœur) ; 8 mutants tués (la règle sans `stepUp`, la preuve remise, le départ d'office, le refus consigné, la page de décision, les greffons du worker, la ligne pas encore visible, l'idempotence) |
| S20-09 | S20 | ✅ | — | un gabarit livre ce qu'un greffon installe (ADR 0036) : `defaults.extensions` (contrat `template.schema.json` : un nom d'installateur → un dossier du gabarit), lu comme une skill — jamais hors du gabarit, aucun lien symbolique — et remis, à la naissance du projet et dans sa transaction, à l'installateur qu'un greffon déclare sous ce nom (`declarer_un_installateur_de_gabarit` ; deux greffons ne prennent pas le même) ; un refus de l'installateur fait échouer la naissance ; SANS installateur, le projet naît sans, et l'audit le dit (`template.extension.skip`, `template.extension.install` sinon). Le greffon de l'ontologie installe `ontology` (`publier_l_ontologie`, partagée avec `PUT /projects/{id}/ontology`). `joiners-leavers` livre son registre, `rh-arrivees-departs` : `collaborateur`, `contrat`, `account` (et ses groupes), `group`, `materiel`, `badge`, leurs liens, 28 outils MCP générés ; cinq actions — consigner l'arrivée, le compte, le poste, le badge (risque faible, la politique décide ; rien pour une personne partie) et constater un départ (compte, badge et poste fermés au registre ; un responsable ré-authentifié). Conformité des gabarits étendue (28 cas) : une ontologie livrée compile sans erreur NI avertissement, ses effets et ses preuves sont servis par le greffon, les agents qu'elle autorise sont livrés, une extension inconnue échoue. `test_gabarit_extensions.py` (6 cas : l'installateur reçoit le dossier, sans lui le projet naît sans, un refus empêche la naissance, un dossier hors du gabarit ou un lien symbolique est refusé, un nom pris deux fois), `test_gabarit_rh.py` (greffon actif, Temporal de test, 3 cas : le projet RH naît avec son registre ; l'arrivée et le badge consignés d'office, un départ attend une personne ; rien ne se consigne pour une personne partie) ; 21 mutants tués (un réécrit). Ne prouve pas que les workflows écrivent eux-mêmes au registre : ils agissent sur l'annuaire, le parc et les lecteurs, le consigner reste le geste de l'agent ou de la RH — la réconciliation quotidienne attend ; ni le registre interrogé depuis le Claude d'une RH sur le dev (la release du lot 6) |
| S20-10 | S20 | ✅ | — | les propositions de l'ontologie se lisent et se décident dans les actions : une proposition est une action du cœur depuis S20-08, sous le MÊME identifiant — l'onglet **proposals** et ses deux pages disparaissent, `/p/<projet>/proposals[/<id>]` redirige (308) vers `/p/<projet>/actions[/<id>]` : un lien de décision émis avant (porte MCP, courriel) mène toujours à la bonne page. La page d'une action d'origine `ontology` dit ce qu'elle touche — type d'action, cibles, paramètres (`params.ontologie`) — puis ce que ses effets ont rendu et ses preuves recueilli (`result`) : le cœur n'en voit que `ontology.effet` et `ontology.preuve`. La décision ne promet plus une authentification récente quand la règle n'en exige pas (`step_up_minutes` absent, S20-08) : elle annonçait « within 10 min » à tort. La console ne lit plus les routes `/proposals` (elles restent servies, vues sur les actions). vitest `action-ontologie.test.tsx` (5 cas : la carte, la page qui la montre, aucune carte hors ontologie, la règle sans authentification récente, les redirections), `api.test.ts` (la ré-authentification passe par la décision d'une action), e2e `parcours` (l'ancien lien mène à l'action, qui dit ce qu'elle touche et se décide) et `accessibilite` (la page d'une action de l'ontologie) ; 8 mutants tués. Ne prouve pas la console sur le dev (la release du lot 6) |
| S20-11 | S20 | ✅ | — | un projet hérite de ce que son déploiement sait faire tourner, et un run mort dit pourquoi (issue #245, trouvée en jouant le scénario RH sur le dev : le premier agent du gabarit n'y a jamais atteint son modèle — le projet neuf partait sur `claude-code`, le dev sert opencode). `global.agents` (`defaultBackend`, `allowedBackends`, `modelProfiles`) devient trois réglages de l'API (`CHOREGOS_DEFAULT_AGENT_BACKEND`, `CHOREGOS_ALLOWED_AGENT_BACKENDS`, `CHOREGOS_DEFAULT_MODEL_PROFILES`) ; un projet né sans section `agent` ni `models` en hérite, un projet qui les nomme les garde, un backend seul est aussi le seul permis. L'exécuteur `k8s_job` lit, sur un Job échoué, le DERNIER pod du run : le code de sortie du runner et son sens (`choregos_core.sorties`, la table des codes passée du runner au cœur pour être lue des deux côtés), ou la raison pour laquelle le conteneur n'a jamais démarré — jamais les journaux, qui peuvent tout contenir. `test_defauts_du_deploiement.py` (4 cas), `test_k8s_job.py` (3 cas de plus : code et sens, pod jamais démarré, aucun pod ; la liste des pods n'a pas d'ordre garanti), `tests/charts/test_defauts_d_agents.py` (2 cas) ; 11 mutants tués (un tué après un test renforcé). Vérifié sur le dev en 0.16.2 (choregos-deploy #24, `global.agents` : opencode, profils `standard` et `cheap`) : le projet `essai-defauts`, créé sans section `agent` ni `models`, est né sur opencode avec les deux profils. Ne prouve pas la raison d'un pod mort sur le dev (aucun run n'y est mort depuis) ; les projets déjà créés gardent leur configuration |
| S21-01 | S21 | ✅ | — | les grilles arbitraires de la console se lisent : Tailwind 4 recopie la valeur telle quelle, et `lg:grid-cols-[2fr,1fr]` devenait `grid-template-columns: 2fr,1fr`, que le navigateur rejette — l'éditeur YAML et la carte retombaient sur une colonne, leur panneau de validation ou d'édition passait DESSOUS (revue de la console du dev, 07/10). Six grilles corrigées (`_` sépare les pistes : éditeur YAML, carte, deux listes de workflows, page d'un agent, page d'une skill), vérifié en compilant les deux formes avec `@tailwindcss/postcss`. `tests/grilles-tailwind.test.ts` parcourt `src/` et refuse toute virgule entre pistes hors d'une fonction (`minmax(0,1fr)` reste permis) ; rouge sur l'arbre d'avant (six fautes). Ne prouve pas l'affichage sur le dev (la release suivante) |
| S21-02 | S21 | ✅ | — | le mode démo de la console parle anglais, comme l'interface (revue du 07/10 : « que de l'anglais, pour toute la plateforme et pour la démo ») : tickets, timeline, runs, preuves, findings, mémoire, unités DORA, workflow de démonstration et sa vue processus (phrases reprises de `process.py`), agents (`onboarding-coordinator`, le Claude Code de Léa), skill (`onboarding-procedure`), opérations des connecteurs (`read_user`, `create_account`, `order_laptop`…, connecteur `supplier-agent`), actions ; une erreur de l'API sans détail se dit `request failed (HTTP n)` au lieu de `Erreur n`. `tests/mocks-en-anglais.test.ts` parcourt chaque chaîne exportée par les fixtures (accents, mots-outils français ; les noms propres gardent leurs accents) — rouge sur les fixtures d'avant (vérifié) ; `tests/api-erreurs.test.ts`. Parcours e2e et accessibilité réalignés (61 cas verts sur le build de production). Ne traduit pas ce que le SERVEUR envoie (noms d'états des gabarits livrés, étiquettes de la carte, messages du validateur et de l'API) : c'est S21-08 à S21-13 |

**Total** : 143 livrées, 12 partielles, 4 non commencées.

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

35. **La console dit sur quelle édition elle tourne : `GET /edition` servie sous `/api/v1`**
    (2026-10-05).

    Le contrat place `/edition` sous `/api/v1` ; l'API ne la servait qu'à la racine, que l'Ingress
    envoie à la console. La console ne pouvait donc pas savoir si elle tournait en édition
    communautaire ou entreprise, et `/admin` ne montrait rien de l'édition entreprise. La route est
    désormais servie sous le préfixe (la racine reste, hors schéma, pour les sondes) ; la barre du
    haut porte un badge d'édition, et `/admin` une carte qui liste ce que l'édition s'autorise — ou,
    en communautaire, ce que l'édition entreprise ajouterait, sans faux écran.

    **Ce que ça prouve** : `test_l_edition_se_demande_sous_le_prefixe_du_contrat` (404 sans le
    changement) ; trois tests Vitest de la carte et du badge ; le parcours Playwright voit le badge.
    **Ce que ça ne prouve pas** : aucun écran des fonctions de l'édition entreprise — c'est le flux
    S17 (sections d'administration déclarées par manifeste).

36. **Les groupes d'un projet ne se changent que par l'administrateur de l'organisation**
    (2026-10-05).

    L'ADR 0014 tient l'accès aux outils du catalogue par deux verrous : le déploiement dit qui a le
    droit (`groups` sur l'outil), le projet dit ce dont il se sert (`config.tools`). Mais
    `config.groups` vivait dans la configuration du projet, que son propriétaire modifie : il
    pouvait s'ajouter au groupe d'un autre métier et en obtenir les outils. Changer les groupes
    exige désormais la permission `tools:grant`, que seul l'administrateur de l'organisation porte ;
    changer `tools` à groupes égaux reste à l'équipe du projet.

    **Ce que ça prouve** : `test_groupes_d_outils.py` — un propriétaire de projet qui s'ajoute un
    groupe reçoit 403 (200 sans le changement) ; il garde la main sur ses outils ; l'administrateur
    change les groupes. La matrice RBAC reste verte.
    **Ce que ça ne prouve pas** : la politique par opération des connecteurs (flux S19), qui
    remplacera ces deux listes.
