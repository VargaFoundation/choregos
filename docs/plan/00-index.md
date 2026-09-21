# Choregos — plan d'exécution complet (v3)

> Plateforme de delivery agentique de la **Varga Foundation** : un ticket entre, une mise en production maîtrisée sort. Ce document est écrit pour être **donné tel quel à Claude Code** (ou à tout agent ACP) comme source unique : architecture, contrats, backlog, conventions, déploiement. Version 3 — 18 septembre 2026.

## 0.1 Comment utiliser ce plan

1. Créer le monorepo `VargaFoundation/choregos` et y copier ce plan dans `docs/plan/` (un fichier par section, `08-backlog.yaml` tel quel).
2. Copier `AGENTS.md` et `CLAUDE.md` de la section 7.6 à la racine.
3. Lancer **une session Claude Code par flux de travail** (section 7.2), chacune dans son `git worktree`, avec pour consigne : « Lis `docs/plan/00-index.md`, puis la section de ton flux, puis tes stories dans `docs/plan/08-backlog.yaml` (filtre `stream: Sx`). Travaille story par story, une PR par story, tests inclus. Ne modifie jamais `packages/contracts` sans une PR dédiée taguée `contract-change`. »
4. Une session **intégratrice** relit, fait tourner la CI, fusionne via la merge queue et met à jour `docs/plan/STATUS.md`.
5. Les sections sont autoportantes : un agent qui ne lit que la sienne et les contrats (section 1) doit pouvoir livrer.

## 0.2 Le nom

La fondation nomme ses projets d'un mot grec chargé de sens : **Argus** (le gardien aux cent yeux — le driver qui voit dans tous les entrepôts), **Ecphoria** (ἐκφορία, le rappel en mémoire). Le troisième projet dirige des agents ; il lui faut un nom de la même famille.

| Nom | Sens | Pourquoi | Prononciation | Collision |
| :-- | :-- | :-- | :-- | :-- |
| **Choregos** (χορηγός) — **recommandé** | Celui qui organise et mène le chœur au théâtre d'Athènes ; celui qui donne les moyens à la représentation | La plateforme dirige un chœur d'agents (OpenHands, Claude Code, Codex, Gemini…) et leur donne workspace, modèle, budget et partition ; un rôle, comme Argus | ko-RÉ-gos | Faible dans la tech ; en grec moderne « sponsor » |
| **Teleia** (τελεία) | Le point final ; l'achèvement | Le problème d'origine (« la tâche n'est jamais vraiment finie ») ; même finale en -ia qu'Ecphoria | té-LÉ-ya | Proche de « Telia » (télécom) |
| **Poiesis** (ποίησις) | L'acte de faire advenir | De l'idée à la production | poï-É-sis | Collectifs artistiques |
| **Poreia** (πορεία) | La marche, le trajet | Le trajet du ticket jusqu'à la prod | po-RÉ-ya | Faible |
| **Eunomia** (εὐνομία) | Le bon ordre, la bonne gouvernance | La couche de politique et le train sérialisé | eu-no-MI-a | Faible |
| **Entelecheia** (ἐντελέχεια) | La réalisation d'un potentiel (Aristote) | Une idée devenue réelle | en-té-lé-KHÉ-ya | Faible, mais long |

Décision de travail : **Choregos**. Vocabulaire optionnel pour la communication (jamais dans le code, où l'on garde l'anglais technique) : *Skene* (σκηνή, la coulisse : le workspace), *Stolos* (στόλος, la flotte qui part ensemble : le batch du release train), *Kleis* (κλείς, la clé : le verrou de prod), *Heurema* (εὕρημα, la trouvaille : un finding), *Taxis* (τάξις, l'ordonnancement : le DSL de workflow). À vérifier avant annonce : INPI/EUIPO (classes 9, 42), domaines `choregos.dev` / `.io`, org GitHub, PyPI `choregos`, npm `@choregos/*`, Docker Hub / ghcr. Renommer = un `sed` sur le monorepo ; rien dans ce plan n'en dépend structurellement.

## 0.3 Décisions fixées (ne pas rouvrir sans PR `decision`)

| # | Décision | Valeur |
| :-- | :-- | :-- |
| D1 | Licence | Apache 2.0 (alignée Argus et Ecphoria) |
| D2 | Contrat agent | ACP (Agent Client Protocol) ; agent par défaut OpenHands (remplacé par Claude Code, [ADR 0011](../adr/0011-retrait-d-openhands.md)) ; Claude Code, Codex, Gemini CLI, Goose, OpenCode, Copilot CLI en backends optionnels |
| D3 | Moteur d'orchestration | Temporal (self-hosted via Helm + CloudNativePG ; Temporal Cloud possible sans changer le code) |
| D4 | Langages | Python 3.12 (`uv`, FastAPI, Temporal SDK, pydantic v2) pour API, orchestrateur, runner, adaptateurs, CLI ; TypeScript (Next.js 15, React 19) pour le front ; Rust pour Ecphoria (existant) |
| D5 | Exécuteur jour 1 | Tekton Pipelines sur Kubernetes ; `Executor` abstrait, Job K8s en repli |
| D6 | Gateway modèles | LiteLLM proxy ; une clé virtuelle par run ; coût compté au gateway |
| D7 | Mémoire | Ecphoria (recentré mémoire + base de connaissance) ; repli pgvector |
| D8 | Tracker / SCM / CI / CD jour 1 | GitHub Issues + Projects v2 / GitHub / Tekton / Argo CD + Argo Rollouts |
| D9 | Déploiement de la plateforme | Kubernetes, Helm umbrella `charts/choregos`, GitOps Argo CD app-of-apps depuis `choregos-infra` |
| D10 | Base de données | PostgreSQL 16 via CloudNativePG, extension `pgvector` |
| D11 | Auth | OIDC (Keycloak en dev, IdP de l'organisation en prod) ; RBAC 5 rôles |
| D12 | Observabilité | OpenTelemetry → Prometheus / Loki / Tempo / Grafana (kube-prometheus-stack) ; Azure Monitor en option d'export ; Langfuse **optionnel** (phase 2) |
| D13 | Instructions dépôt | `AGENTS.md` (standard) ; `CLAUDE.md` = lien symbolique |
| D14 | Workflow par défaut | `default-simple` ; prod toujours via release train |
| D15 | Monorepo | `VargaFoundation/choregos` ; infra GitOps dans `VargaFoundation/choregos-infra` ; Ecphoria reste dans son dépôt |

## 0.4 Carte du document

| Section | Fichier | Contenu | Flux concernés |
| :-- | :-- | :-- | :-- |
| 1 | `01-architecture-et-contrats.md` | Architecture, dépôt, modèle de données, DSL, StageInput/StageResult, événements, API, interfaces d'adaptateurs | tous |
| 2 | `02-orchestrateur-agents-runner.md` | Workflows Temporal, client ACP, backends, runner, Task Tekton, sandbox, playbooks | S1, S2 |
| 3 | `03-connecteurs-templates-front.md` | Connecteurs, templates, provisioning, front, API | S3, S5, S6, S8 |
| 4 | `04-modeles-couts-memoire.md` | Gateway, profils, coûts dans les tickets, Ecphoria (étude, améliorations, intégration) | S4, S10, S11 |
| 5 | `05-release-train-findings.md` | Release train, GitOps, findings | S9, S10 |
| 6 | `06-deploiement-kubernetes.md` | Déploiement de la plateforme sur Kubernetes : charts, namespaces, HA, sauvegardes, sécurité, environnements | S7 |
| 7 | `07-plan-parallelisation-conventions.md` | Flux parallèles, jalons, sprints, mode opératoire Claude Code, `AGENTS.md`/`CLAUDE.md`, DoD, risques | tous |
| 8 | `08-backlog.yaml` | Epics et stories machine-lisibles | tous |

## 0.5 Glossaire

- **WorkItem** : représentation canonique d'un ticket, quel que soit le tracker.
- **Workflow (DSL)** : machine à états déclarée en YAML par projet ; états, transitions, acteurs, gates.
- **Stage / run** : exécution d'une transition « agent » par un runner ; a un ID, un coût, un transcript, un `StageResult`.
- **Gate** : condition déterministe sur une transition (CI verte, scans OK…).
- **Release train** : workflow singleton par environnement qui sérialise les déploiements.
- **Finding** : problème découvert hors périmètre, transformé en ticket lié.
- **Context pack** : sélection de mémoire, tickets liés et incidents injectée dans un stage sous budget de tokens.
- **Backend agent** : implémentation ACP (OpenHands, Claude Code…) lancée dans le workspace.
- **Executor** : ce qui fait tourner un run (Tekton PipelineRun, Job K8s, ACA Job).
- **Template (stack)** : combinaison de connecteurs + workflow + politique + scaffolding, instanciable en un projet.
