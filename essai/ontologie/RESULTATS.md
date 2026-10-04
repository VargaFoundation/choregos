# Essai, éléments 1 à 6 — résultats du 2026-10-03

**Question** : le cœur de Choregos peut-il porter l'ontologie et le moteur d'actions du futur socle
**par ses coutures**, sans fork ? Concrètement, sur la tranche IT4IT :
- un paquet YAML validé et compilé (élément 1) ;
- des constats synchronisés depuis un rapport observations NDJSON v1 (élément 2) ;
- lus par un agent au moyen des outils MCP générés, avec son jeton de run (élément 3) ;
- l'agent propose une action dont l'effet est `gitops.pull_request` (élément 4) ;
- un humain valide avec ré-authentification, et la plateforme ouvre la PR (élément 5) ;
- la preuve est la relance du collecteur, clé du constat absente (élément 6).

**Réponse : oui, au prix de quatre changements du cœur, tous petits et testés** : une couture pour les
outils de greffon, un filtre sur ses migrations, l'écriture de fichiers par l'adaptateur SCM, et une
heure d'authentification digne de ce nom. Le reste passe par les coutures existantes : routes de
greffon, branche de migrations, jeton de run, chemin d'outils de l'agent, adaptateurs, audit.

## Montage

- `packages/ontology` : la partie sans I/O (modèle, chargement, validation, compilation, outils
  générés, observations, évaluation CEL) ; `choregos_ontology.service` : le **greffon**, servi par
  l'API du cœur (extra `service`).
- Le greffon est déclaré par le `pyproject.toml` du paquet (`choregos.plugins` → `brancher()`,
  `choregos.migrations` → sa branche Alembic), et **livré inactif** : il ne s'active qu'avec
  `CHOREGOS_ESSAI_ONTOLOGIE=1` (dans le chart, `global.extraEnv`). Inactif, il ne sert aucune route,
  n'annonce aucun outil, et sa branche de migrations est vide. C'est `create_app()` qui le charge, et
  `python -m choregos_api.migrer` qui joue sa branche.
- Ses tables : `ontology_versions`, `managed_objects` (propriétés en JSON, clé projet + type + id) et
  `action_proposals`, **sous RLS forcée** sur PostgreSQL, rattachées à l'organisation par leur projet
  comme les tables du cœur.
- Ses routes : `PUT/GET /projects/{id}/ontology`, `POST /projects/{id}/observations`,
  `GET /projects/{id}/objects/{type}`, `GET /projects/{id}/proposals[/{p}]` et
  `POST /projects/{id}/proposals/{p}/decision`, sous les droits du cœur.
- Ses outils : par la **nouvelle couture** `declarer_un_fournisseur_d_outils`, ils arrivent chez
  l'agent par le chemin du catalogue : `GET /internal/runs/{id}/tools` les annonce au serveur MCP
  `choregos-tools` du runner, `POST /internal/runs/{id}/tools/{nom}` les appelle sous le jeton du run,
  avec le plafond d'appels par run, une ligne au registre des coûts et l'événement `tool.called`.
- Le paquet de la tranche IT4IT (`packages/ontology/tests/fixtures/it4it`) : `finding`, `host`,
  `service`, l'action `open_infra_pr` (effet `gitops.pull_request`, chemins `platform/**` et
  `gitops/**`, preuve : la PR est ouverte sur la bonne branche), l'action `verify_finding_fixed` (preuve
  `collector.rerun`, clé absente exigée) et la politique `infra_change` (risque faible : d'office ;
  sinon un `owner`, authentification de moins de 10 minutes).

## Ce qui a été prouvé

**Éléments 1 à 3**

| # | Contrôle | Résultat |
|---|---|---|
| 1 | Le paquet `core-ref` est validé, compilé, et devient la version active ; un second dépôt remplace le premier ; 15 outils générés | ✅ |
| 2 | Un paquet invalide est refusé (422) avec ses erreurs localisées, et ne remplace pas la version active | ✅ |
| 3 | Un nom de fichier qui sort du paquet (`..`, chemin absolu, non YAML) est refusé | ✅ |
| 4 | Un rapport ouvre un constat par (`layer`, `check`) avec toutes ses portées ; **son rejeu n'écrit rien**, pas même une version de ligne | ✅ |
| 5 | **Un rapport partiel n'écrit rien** : sans ligne `_end`, compte faux, ligne invalide — même quand il aurait résolu et ouvert des constats | ✅ |
| 6 | Seul `ok` sur la même portée résout ; `unreachable` et une couche absente ne résolvent rien ; une contradiction dans un rapport garde la portée ouverte | ✅ |
| 7 | Un lecteur (`viewer`) lit les objets mais ne synchronise pas (403) | ✅ |
| 8 | Le run voit les outils que l'essai sait servir, et eux seuls : ni `alert_*` (datasource `connector`), ni une action dont un effet n'est pas servi | ✅ |
| 9 | L'agent lit les constats (`finding_search`), un hôte (`host_get`), ses services et le porteur d'un service (liens) ; **le journal de la plateforme** (`tool.called`) porte ces appels, dans l'ordre | ✅ |
| 10 | Une propriété `confidential` (`ip`) est absente des résultats **et des filtres** | ✅ |
| 11 | Un argument hors schéma est refusé (400) ; sans jeton, 401 ; avec le jeton d'un autre run, 403 | ✅ |
| 12 | Le run d'un autre projet ne voit que ses objets | ✅ |
| 13 | **Par le vrai serveur MCP de l'agent** (`choregos-tools`) : `tools/list` rend ses outils et ceux de l'ontologie, `tools/call host_get` rend l'hôte sans son adresse | ✅ |
| 14 | La branche de migrations crée les tables après le cœur, et redescend seule | ✅ |
| 15 | Sur PostgreSQL : RLS forcée sur les trois tables ; une organisation ne voit ni les objets ni l'ontologie de l'autre ; une session sans organisation ne voit rien ; une écriture dans le projet d'une autre organisation est refusée | ✅ |

**Éléments 4 à 6**

| # | Contrôle | Résultat |
|---|---|---|
| 16 | L'agent propose `open_infra_pr` par son outil, avec son jeton : proposition **`pending_approval`, avec sa justification**, proposée par `agent:platform` au nom du run ; **rien n'est ouvert** avant la validation | ✅ |
| 17 | La même proposition deux fois rend la première (clé d'idempotence) | ✅ |
| 18 | Un chemin hors de `paths_allowed` (ou qui remonte par `..`) est refusé **à la proposition** ; une précondition fausse (constat résolu) aussi ; rien n'est enregistré | ✅ |
| 19 | Valider avec une authentification de plus de 10 minutes : **401 vers `?reauth=1`**, aucune PR ; après la ré-authentification : la plateforme écrit le fichier sur `choregos/<proposition>`, ouvre la PR, et la preuve la constate (`succeeded`) | ✅ |
| 20 | **La décision est dans le dossier avec son `auth_time`**, celui de la session ouverte par `?reauth=1` | ✅ |
| 21 | Un développeur ne valide pas une action qui exige un `owner` (403) ; un rejet clôt sans effet ; une décision ne se reprend pas (409) | ✅ |
| 22 | Si le SCM rend une autre branche que celle attendue, la preuve échoue et l'action est `failed` | ✅ |
| 23 | `verify_finding_fixed` (risque faible, approuvée d'office) attend le rapport suivant ; **sans la clé, `succeeded`**, et le fait `collector_clean` vaut `true` | ✅ |
| 24 | **Clé encore présente, couche absente, couche `unreachable` : `failed`**, jamais « clé absente » | ✅ |
| 25 | Un rapport partiel fait échouer la preuve qui l'attendait, **sans écrire aucun objet** ; sans rapport dans les 15 minutes, `failed` | ✅ |
| 26 | Par le vrai serveur MCP de l'agent, la proposition arrive `pending_approval` | ✅ |
| 27 | Une décision exige une session humaine : un jeton d'API est refusé (403 `decision_requires_session`) ; un refus exige un motif (422) ; la séparation des rôles rend 422 ; une authentification trop ancienne rend 401 `step_up_required` (contrat 03 §12, R-SOC-ACT-04) | ✅ |
| 29 | Une proposition humaine (`POST /projects/{id}/proposals`) suit les mêmes règles que celle d'un agent ; le droit vient des rôles (`role:contributor`), un lecteur est refusé (403), des paramètres hors schéma aussi (422) ; l'inventaire (`register_host`) s'écrit par le moteur d'actions | ✅ |
| 30 | Une action que l'agent n'a pas le droit de proposer n'est pas dans sa liste d'outils, et l'appeler répond comme un outil inexistant (404) | ✅ |
| 31 | Sans `CHOREGOS_ESSAI_ONTOLOGIE`, le greffon est inerte : aucune route, aucun outil, aucune table | ✅ |
| 28 | **Contre la pile intégrée qui tourne** (PostgreSQL sous RLS, uvicorn, Temporal) : les éléments 1 à 6 de bout en bout — voir `essai/demarrage-local/RESULTATS.md` | ✅ |

Rejouer : `uv run pytest packages/ontology apps/api/tests/test_greffons_outils.py
apps/api/tests/test_authentification_fraiche.py packages/adapters/tests/test_github_scm.py`
(les tests PostgreSQL demandent `CHOREGOS_TEST_DATABASE_URL`). Chaque garde a été retirée une fois
pour vérifier qu'un test rougit : collision d'outils, `project:write`, filtrage par sensibilité,
filtre des migrations du cœur, fraîcheur de l'authentification (moteur et cœur), `paths_allowed`,
rôle de l'approbateur, préconditions, couche absente, couche `unreachable`.

## Ce que l'essai a dû changer dans le cœur

1. **Une couture : les outils de greffon** (`greffons.declarer_un_fournisseur_d_outils` et
   `routers/internal.py`). Sans elle, un greffon pouvait servir des routes, mais aucun outil n'arrivait
   chez l'agent : le catalogue ne se lisait que dans un fichier du déploiement. Un nom d'outil déjà pris
   (par le catalogue ou un autre greffon) est **refusé en 409**, pas masqué.
2. **Les migrations du cœur ne décrivent que ses tables** (`db.models.objet_du_coeur`). Un greffon
   inscrit ses modèles dans le `Base` du cœur — l'édition entreprise fait de même — : sans ce filtre,
   `test_migrations.py` rougit dès qu'un greffon a été importé plus tôt dans la session de tests
   (constaté), et une autogénération lancée avec un greffon installé ferait entrer ses tables dans une
   migration du cœur. **À reporter dans `choregos-ee`.**
3. **L'adaptateur SCM sait écrire des fichiers** (`ScmAdapter.commit_files`, GitHub par l'API
   Contents, et le faux). Sans lui, seule la PR d'un agent qui pousse lui-même était possible ; l'effet
   `gitops.pull_request` exige que la plateforme écrive, jeton gardé. Idempotent : un fichier identique
   n'est pas réécrit, une PR ouverte sur la branche est réutilisée (le faux le fait désormais comme
   GitHub).
4. **L'heure d'authentification est celle de l'IdP** (`auth_time` de l'ID token, exigé récent après
   `?reauth=1`). `authentifie_le` lisait l'`iat` de la session : une reconnexion SSO silencieuse
   produisait une heure neuve sans aucune authentification, et toute porte « authentification
   récente » — celle de l'élément 5, le contrôle de fraîcheur de l'édition entreprise — était
   contournable. **Issue #153** pour la ligne principale.

## Ce que l'essai ne prouve pas

- **Qu'un modèle choisisse ces outils** : aucun LLM dans la boucle. Le chemin agent → `choregos-tools`
  → API interne → greffon est réel ; le choix des appels est écrit par le test.
- **La console** : la décision passe par l'API, pas par un écran.
- **Un vrai GitHub** : la PR est ouverte sur le faux du cœur ; l'écriture par l'API Contents a ses
  tests d'adaptateur, pas d'essai contre un dépôt réel.
- **La reprise sur panne** : le moteur s'exécute dans la requête, pas dans `ApprovalWorkflow` et
  `ActionWorkflow` (Temporal). Une panne entre l'ouverture de la PR et la preuve laisse la proposition
  `running`.
- **Le déclenchement du collecteur** : la preuve `collector.rerun` attend le rapport suivant, mais
  l'essai ne déclenche rien (pas de cadre de connecteurs) ; le rapport est posté par le test.
- **La synchronisation par le moteur d'actions** : c'est une route, pas l'action système
  `connector.sync` sous autorisation permanente (R-SOC-CON-04). Le fait `observations_partial_read`
  n'est pas publié (422 seulement) : publier un événement demande un type au contrat, donc une PR
  `contract-change`. Les décisions et changements d'état vont au journal d'audit du cœur.
- **Le passage à l'échelle des requêtes** : les filtres s'évaluent en Python sur les objets du type ;
  la spec demande des index d'expression (R-SOC-ONT-16).
- **Le plan et l'application en deux temps d'une version d'ontologie** (SOC-010).
- **Les hôtes et services** sont posés en base par le test : seule la synchronisation des constats
  existe.

## Écarts avec le cahier des charges

- `managed_objects` sans colonne `org_id` : la RLS la déduit du projet, comme le cœur. À trancher
  dans la spec (10 §1.2).
- Deux statuts de version seulement (`active`, `superseded`) au lieu de sept.
- Les outils d'un greffon ne coûtent rien au registre (`cost_eur = 0`) : la spec ne dit pas si une
  lecture d'objet a un prix.
- Tout run propose sous l'identité `agent:platform` : les identités d'agents (R-SOC-MCP-03)
  n'existent pas.
- L'action de vérification a besoin d'un effet (`effects` en exige au moins un) : `verify_finding_fixed`
  date la demande sur le constat (`verification_requested_at`). La spec devrait dire si une action
  sans effet, réduite à sa preuve, est permise.
- Le modèle du cœur ne distingue pas une PR fermée sans fusion (`PrState` n'a que `merged`) : la
  preuve rend `open` ou `merged`.
- `open_infra_pr` est déclarée `reversible` et non `compensable` : la compensation « fermer la PR »
  de la spec (AGT-014) demande un effet que l'essai ne sert pas.

## Effort

Mesuré en volume, pas en temps (le travail a été fait par un agent, sans chronométrage) :
- éléments 2 et 3 : ~980 lignes de code du greffon, ~980 lignes de tests, **115 lignes ajoutées dans
  le cœur** ;
- éléments 4 à 6 : ~1 000 lignes de code du greffon (dont le moteur d'actions et l'évaluation CEL),
  ~460 lignes de tests, le paquet IT4IT, et **110 lignes ajoutées dans le cœur** (écriture SCM,
  heure d'authentification), avec leurs tests.

Le rapport de décision du J0 (TRV-010) devra rapporter ce volume à l'estimation des stories
correspondantes.
