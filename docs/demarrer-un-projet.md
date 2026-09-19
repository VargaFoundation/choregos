# Intégrer un projet à Choregos

Ce document répond à une seule question : **j'ai un dépôt applicatif, comment le fais-je
développer par la plateforme ?**

Trois chemins, du moins engageant au plus réel. Prenez le premier si vous voulez voir ce
que ça donne avant d'installer quoi que ce soit.

---

## 1. Voir le trajet complet sans rien installer

```bash
make setup
make demo
```

`make demo` joue un ticket de bout en bout sur des adaptateurs simulés : spécification,
validation, implémentation, vérification, PR, merge, déploiement. Vous verrez le
commentaire de suivi tel qu'il serait écrit dans le ticket — étapes, backend et modèle
par étape, tokens, coût, durée — et le récapitulatif :

```
· runs exécutés        : 3
· coût total           : 1.00 USD
· PR ouverte           : https://fake.scm/varga/billing-api/pull/1
· findings déposés     : 1
· état final du ticket : deployed_prod
```

Rien n'est appelé à l'extérieur. C'est la forme du trajet, pas une preuve qu'il marche
contre vos outils.

## 2. Sur un cluster de développement

```bash
make dev-up      # kind + Tilt : la plateforme complète sur votre machine
make dev-seed    # une org, un projet de démonstration, dix tickets, un train
```

Le front est sur `http://localhost:3000`, l'API sur `:8000`. Vous pouvez y créer un projet
et suivre un run réel de la plateforme — avec des connecteurs toujours simulés tant que
vous ne configurez pas les vrais.

Sans Kubernetes, `docker compose -f dev/compose.yaml up -d` monte les dépendances
(Postgres, Temporal, LiteLLM, Ecphoria, Keycloak, MinIO) et les trois processus se lancent
à la main — voir le README.

---

## 3. Intégrer un vrai projet

### Ce qu'il faut avoir avant de commencer

| | Pourquoi |
| :-- | :-- |
| Le **dépôt applicatif** (GitHub) | c'est lui que les agents modifient, en branche, par PR |
| Un **dépôt GitOps** | le provisioning y écrit les manifestes du projet, qu'Argo CD applique |
| Un **board Projects v2** | l'état du ticket, le coût, la taille, le risque et le lien du run y sont miroités |
| Une **GitHub App** installée sur l'org | jetons d'installation scopés, webhooks ; jamais un PAT large |
| Un **cluster** (ou un abonnement Azure) | selon le template choisi : runners Tekton, ou jobs Container Apps |
| Les **secrets de la plateforme** | `session-secret`, la paire de clés des jetons de run, le secret de webhook, l'App ID et sa clé privée — dans le coffre, lus par External Secrets (`charts/choregos/charts/api/templates/externalsecret.yaml`) |

Les modèles passent par la passerelle LiteLLM de la plateforme : un projet n'a pas de clé
de fournisseur à lui, il a un budget.

### Choisir un template

| Template | Quand le prendre |
| :-- | :-- |
| `github-tekton-argo-k8s` | vous avez un cluster et vous y faites tourner les runners |
| `github-aca` | vous êtes sur Azure et vous ne voulez pas de pool Kubernetes pour les agents : les runs sont des *jobs* Container Apps, facturés à la seconde |

`templates/<nom>/manifest.yaml` liste ses entrées, ses connecteurs requis et ses étapes.

### Créer le projet

```bash
choregos login --api-url https://choregos.example.com --org acme
choregos projects create billing-api \
  --repo https://github.com/acme/billing-api \
  --language python \
  --template github-tekton-argo-k8s
```

`--template` enchaîne sur le provisioning. Pour le relancer et le suivre :

```bash
choregos projects provision billing-api --follow
```

Le provisioning est **idempotent** : le relancer après un échec reprend là où il en était,
sans doubler ce qui a déjà été fait.

### Ce que le provisioning fait

Installation de la GitHub App, labels, board Projects v2 avec ses champs, gabarit
d'issue, manifestes écrits dans le dépôt GitOps puis synchronisés par Argo CD, webhooks,
tenant mémoire du projet avec un import initial (README, docs, ADR, issues et PR de
l'année), équipe et budget à la passerelle, et un message de test sur le canal de
notification.

Et une **PR de scaffolding** sur votre dépôt :

| Fichier | À relire avant de merger |
| :-- | :-- |
| `AGENTS.md` | les commandes de test, lint et typage de *votre* projet |
| `.choregos/workflow.yaml` | les libellés d'état doivent correspondre à votre board ; gardez les gates |
| `.choregos/policy.yaml` | budgets, approbations, périmètre autorisé, règles du train |
| `tekton/pipeline.yaml` | la CI du projet |
| `deploy/kustomization.yaml` | la cible de déploiement |
| `.github/PULL_REQUEST_TEMPLATE.md`, `CODEOWNERS` | la revue |

Validez le workflow avant de committer :

```bash
choregos workflow validate .choregos/workflow.yaml
```

### Le premier ticket

Ouvrez une issue, décrivez l'intention, posez le label **`agent-ready`**. C'est le seul
déclencheur. La plateforme :

1. classe le ticket (taille, risque) et choisit le workflow ;
2. lance un run de spécification — l'agent écrit une spec avec des critères vérifiables ;
3. attend la validation humaine si la politique du projet l'exige ;
4. implémente en branche, dans les seuls chemins autorisés par le ticket ;
5. vérifie (tests, lint, typage, couverture) et ouvre la PR ;
6. miroite tout dans le ticket : état, coût, lien du run.

Si un webhook se perd, un rattrapage périodique reprend les tickets `agent-ready` ouverts
que personne n'a vus — vous n'avez rien à faire.

### Suivre et reprendre la main

```bash
choregos items list billing-api --state running
choregos runs tail <run-id>        # le journal du run, en direct
choregos runs diff <run-id>        # ce que l'agent a changé
choregos items approve <item-id>   # débloquer une attente humaine
choregos items reject  <item-id> --reason "…"
```

---

## Ce qui n'est pas encore éprouvé

`docs/plan/BLOCKERS.md` tient la liste à jour, avec la cause et le contournement retenu
pour chacun. Au moment d'écrire : le backend **OpenHands** n'a jamais été confronté à son
vrai binaire (le défaut du projet reste donc à choisir parmi les autres backends), le
connecteur **Jira** n'a jamais reçu de réponse d'une instance réelle, et les valeurs de
haute disponibilité de la plateforme demandent un environnement de la taille de staging.

## Où aller ensuite

- `docs/dev.md` — monter un environnement de développement
- `docs/securite.md` — ce que la plateforme garantit, et par quel mécanisme
- `docs/runbooks/` — les gestes d'exploitation
- `docs/plan/02-orchestrateur-agents-runner.md` — les backends d'agents et leurs invocations
