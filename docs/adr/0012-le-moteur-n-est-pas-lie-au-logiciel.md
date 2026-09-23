# 0012 — Le moteur n'est pas lié au logiciel

- **Statut** : accepté, 2026-09-23
- **Concerne** : le cœur (DSL, interpréteur, garanties, playbooks), et ce qui en dépend

## Contexte

Choregos est né pour la livraison logicielle : un ticket entre, une mise en production sort.
La question posée est autre — le même cœur peut-il porter du **staffing RH**, de
l'**administratif**, ou tout métier où des demandes arrivent, sont instruites par étapes,
et où quelqu'un doit répondre de ce qui a été fait ?

Ce n'est pas une question de souhait mais d'inventaire : qu'est-ce qui, dans la machine,
parle vraiment de logiciel ? La démonstration `demo/` répond en pratique — deux projets, le
même déploiement, l'un qui écrit du code et l'autre qui qualifie des profils.

## Ce qui ne parle PAS de logiciel, et qui est l'essentiel

| Mécanisme | Pourquoi il est générique |
|---|---|
| Le DSL (états, transitions, acteurs, `on_fail`, délais) | Un graphe d'états nommés par le métier ; rien n'y suppose un dépôt |
| L'interpréteur Temporal | Un workflow par demande, repris après panne, avec pause, reprise et migration de définition |
| Les acteurs `agent` / `human` / `system` | Une validation humaine avec SLA est un besoin de tous les métiers |
| Budgets (tours, minutes, euros) et clé par run | Ce qui borne la dépense d'un agent ne dépend pas de ce qu'il produit |
| La mémoire (Ecphoria) | Des faits, des décisions, des épisodes : rien de spécifique au code |
| L'audit, le coût par étape, la reprise, les évals | Idem |

## Ce qui parlait de logiciel, et ce qu'on en a fait

| Point dur | Décision |
|---|---|
| Les **playbooks** vivaient dans le paquet, pour dix rôles de développement | `CHOREGOS_PLAYBOOKS_DIR` : le déploiement apporte ses rôles (`<rôle>.md`), lus **avant** ceux du paquet. Un ConfigMap suffit, le chart le monte |
| La seule garantie utilisable hors code était `evidence_present`, qui exige des **tests** | Nouvelle garantie `outputs_present` : l'étape a produit ce que la transition déclare (`outputs:`). Mécanique, pas déclarative — c'est ce qui distingue une garantie d'une consigne |
| Le **tracker** était forcément externe (GitHub, Jira) | Connecteur `tracker: internal` : la plateforme tient le ticket, les écritures externes sont des non-opérations franches |
| Le **runner** clone un dépôt et travaille dans un workspace git | Inchangé, et c'est une limite : un métier sans dépôt garde un `repo` factice. Voir plus bas |

## Ce qui reste lié au logiciel, et qu'il faut savoir avant de s'engager

1. **`ProjectConfig.repo` est obligatoire.** Un projet RH déclare un dépôt qui ne sert à
   rien. C'est le défaut le plus visible : il faudrait un projet « sans dépôt », où le
   workspace de l'agent est un répertoire vide.
2. **`StageOutputs` porte des champs de développement** (`allowed_paths`, `spec_markdown`,
   `verdict`…). Le modèle tolère les champs supplémentaires, donc un métier nomme ses
   sorties librement — mais les siennes ne sont pas typées, et la garantie ne peut que
   vérifier leur présence, pas leur forme.
3. **`Evidence` parle de tests, de lint et de couverture.** Un dossier instruit n'a rien de
   tout cela. Il faudrait des preuves nommées par le métier, avec une garantie qui les lise.
4. **Les rôles du DSL sont une énumération fermée** (`triage`, `implement`, `verify`…).
   Un rôle métier s'écrit `role: custom` + `playbook: sourcing` : cela fonctionne, mais le
   board affiche `custom`, et les évals ne savent pas de quoi il s'agit.
5. **Les gates du logiciel restent nombreuses** (`diff_size_max`, `ci_green`, `scans_ok`…).
   Elles ne gênent pas un autre métier, qui ne les déclare pas ; mais l'équilibre montre où
   la plateforme a grandi.

## Décision

Le cœur est **déclaré générique**, et les quatre mécanismes ci-dessus l'ont rendu utilisable
tel quel pour un métier non logiciel. Les cinq limites sont écrites ici plutôt que
découvertes par le premier qui essaiera. La prochaine marche, si un vrai besoin métier
arrive : un projet **sans dépôt**, et des **preuves nommées par le métier**.

## Conséquences

- `demo/workflows/staffing.yaml` tourne sur le même déploiement que la démonstration
  logicielle, sans un seul changement de code entre les deux.
- Un déploiement peut remplacer **n'importe quel** playbook du paquet, y compris
  `implement` : l'adaptation d'un rôle à une maison ne demande pas de modifier Choregos.
- La garantie `outputs_present` donne à tout workflow une garantie mécanique, ce qui évite
  la pente naturelle du « on fait confiance à l'agent » quand il n'y a pas de tests.
