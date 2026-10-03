# Essai, éléments 1 à 3 — résultats du 2026-10-03

**Question** : le cœur de Choregos peut-il porter l'ontologie du futur socle **par ses coutures**, sans
fork : un paquet YAML validé et compilé (élément 1), des constats synchronisés depuis un rapport
observations NDJSON v1 (élément 2), lus par un agent au moyen des outils MCP générés, avec son jeton de
run (élément 3) ?

**Réponse : oui, avec une couture de plus dans le cœur (les outils de greffon) et un filtre sur ses
migrations.** Le reste passe par les coutures existantes : routes de greffon, branche de migrations,
jeton de run, chemin d'outils de l'agent.

## Montage

- `packages/ontology` : la partie sans I/O (modèle, chargement, validation, compilation, outils
  générés, observations) ; `choregos_ontology.service` : le **greffon**, servi par l'API du cœur
  (extra `service`).
- Le greffon s'installe comme le ferait `pip` : un `.dist-info` qui déclare `choregos.plugins`
  (`brancher()`) et `choregos.migrations` (sa branche Alembic). C'est `create_app()` qui le charge, et
  `python -m choregos_api.migrer` qui joue sa branche.
- Ses tables : `ontology_versions` et `managed_objects` (propriétés en JSON, clé projet + type + id),
  **sous RLS forcée** sur PostgreSQL, rattachées à l'organisation par leur projet comme les tables du
  cœur.
- Ses routes : `PUT/GET /projects/{id}/ontology`, `POST /projects/{id}/observations`,
  `GET /projects/{id}/objects/{type}`, sous les droits du cœur (`project:write` pour écrire).
- Ses outils : par la **nouvelle couture** `declarer_un_fournisseur_d_outils`, ils arrivent chez
  l'agent par le chemin du catalogue : `GET /internal/runs/{id}/tools` les annonce au serveur MCP
  `choregos-tools` du runner, `POST /internal/runs/{id}/tools/{nom}` les appelle sous le jeton du run,
  avec le plafond d'appels par run, une ligne au registre des coûts et l'événement `tool.called`.

## Ce qui a été prouvé

| # | Contrôle | Résultat |
|---|---|---|
| 1 | Le paquet `core-ref` est validé, compilé, et devient la version active ; un second dépôt remplace le premier ; 15 outils générés | ✅ |
| 2 | Un paquet invalide est refusé (422) avec ses erreurs localisées, et ne remplace pas la version active | ✅ |
| 3 | Un nom de fichier qui sort du paquet (`..`, chemin absolu, non YAML) est refusé | ✅ |
| 4 | Un rapport ouvre un constat par (`layer`, `check`) avec toutes ses portées ; **son rejeu n'écrit rien**, pas même une version de ligne | ✅ |
| 5 | **Un rapport partiel n'écrit rien** : sans ligne `_end`, compte faux, ligne invalide — même quand il aurait résolu et ouvert des constats | ✅ |
| 6 | Seul `ok` sur la même portée résout ; `unreachable` et une couche absente ne résolvent rien ; une contradiction dans un rapport garde la portée ouverte | ✅ |
| 7 | Un lecteur (`viewer`) lit les objets mais ne synchronise pas (403) | ✅ |
| 8 | Le run voit les outils que l'essai sait servir, et eux seuls (ni `alert_*`, datasource `connector`, ni les actions) | ✅ |
| 9 | L'agent lit les constats (`finding_search`), un hôte (`host_get`), ses services et le porteur d'un service (liens) ; **le journal de la plateforme** (`tool.called`) porte ces appels, dans l'ordre | ✅ |
| 10 | Une propriété `confidential` (`ip`) est absente des résultats **et des filtres** | ✅ |
| 11 | Un argument hors schéma est refusé (400) ; sans jeton, 401 ; avec le jeton d'un autre run, 403 | ✅ |
| 12 | Le run d'un autre projet ne voit que ses objets | ✅ |
| 13 | **Par le vrai serveur MCP de l'agent** (`choregos-tools`) : `tools/list` rend ses outils et ceux de l'ontologie, `tools/call host_get` rend l'hôte sans son adresse | ✅ |
| 14 | La branche de migrations crée les deux tables après le cœur, et redescend seule | ✅ |
| 15 | Sur PostgreSQL : RLS forcée sur les deux tables ; une organisation ne voit ni les objets ni l'ontologie de l'autre ; une session sans organisation ne voit rien ; une écriture dans le projet d'une autre organisation est refusée | ✅ |

Rejouer : `uv run pytest packages/ontology apps/api/tests/test_greffons_outils.py` (91 tests ; les
deux tests PostgreSQL demandent `CHOREGOS_TEST_DATABASE_URL`). Chaque garde a été retirée une fois pour
vérifier qu'un test rougit : collision d'outils, `project:write`, filtrage par sensibilité, filtre des
migrations du cœur.

## Ce que l'essai a dû changer dans le cœur

1. **Une couture : les outils de greffon** (`greffons.declarer_un_fournisseur_d_outils` et
   `routers/internal.py`). Sans elle, un greffon pouvait servir des routes, mais aucun outil n'arrivait
   chez l'agent : le catalogue ne se lisait que dans un fichier du déploiement. Un nom d'outil déjà pris
   (par le catalogue ou un autre greffon) est **refusé en 409**, pas masqué. Testée par
   `apps/api/tests/test_greffons_outils.py`.
2. **Les migrations du cœur ne décrivent que ses tables** (`db.models.objet_du_coeur`, utilisé par
   `migrations/env.py` et `test_migrations.py`). Un greffon inscrit ses modèles dans le `Base` du cœur —
   l'édition entreprise fait de même — : sans ce filtre, `test_migrations.py` rougit dès qu'un greffon a
   été importé plus tôt dans la session de tests (constaté), et une autogénération lancée avec un greffon
   installé ferait entrer ses tables dans une migration du cœur. **À reporter dans `choregos-ee`.**

## Ce que l'essai ne prouve pas

- **Qu'un modèle choisisse ces outils** : aucun LLM dans la boucle. Le chemin agent → `choregos-tools`
  → API interne → greffon → objets est réel ; le choix des appels est écrit par le test.
- **La synchronisation par le moteur d'actions** : c'est une route, pas l'action système
  `connector.sync` sous autorisation permanente (R-SOC-CON-04). Le fait `observations_partial_read`
  n'est pas publié (422 seulement) : publier un événement demande un type au contrat, donc une PR
  `contract-change`.
- **Le passage à l'échelle des requêtes** : les filtres s'évaluent en Python sur les objets du type ;
  la spec demande des index d'expression (R-SOC-ONT-16). Rien n'est mesuré au-delà de quelques
  dizaines d'objets.
- **Le plan et l'application en deux temps d'une version d'ontologie** (SOC-010) : un dépôt valide
  devient actif d'un coup.
- **Les hôtes et services** sont posés en base par le test : seule la synchronisation des constats
  existe.

## Écarts avec le cahier des charges

- `managed_objects` sans colonne `org_id` : la RLS la déduit du projet, comme le cœur. À trancher
  dans la spec (10 §1.2) : garder `org_id` (index, partitionnement) ou suivre le cœur.
- Deux statuts de version seulement (`active`, `superseded`) au lieu de sept.
- Les outils d'un greffon ne coûtent rien au registre (`cost_eur = 0`) : la spec ne dit pas encore si
  une lecture d'objet a un prix.

## Effort

Mesuré en volume, pas en temps (le travail a été fait par un agent, sans chronométrage) : pour les
éléments 2 et 3, ~980 lignes de code du greffon, ~980 lignes de tests, et **115 lignes ajoutées dans le
cœur** (22 retirées). Le rapport de décision du J0 (TRV-010) devra rapporter ce volume à l'estimation
des stories correspondantes.
