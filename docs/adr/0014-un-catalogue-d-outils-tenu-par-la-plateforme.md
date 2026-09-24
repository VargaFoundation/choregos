# 0014 — Un catalogue d'outils tenu par la plateforme

- **Statut** : accepté, 2026-09-24
- **Concerne** : l'API interne, le sidecar MCP, la politique, le registre de coûts

## Contexte

La démonstration RH du 2026-09-23 s'est arrêtée sur « aucune base de profils candidats
accessible ». Les agents ont eu raison de bloquer : ce n'était pas un défaut du moteur, c'était
une absence de **données**. Un agent qui instruit un dossier, cherche un profil ou vérifie une
entreprise a besoin d'API tierces — et ces API ont des clés.

[treg](https://github.com/superdesigndev/treg) répond exactement à ce manque : un annuaire de
3 000 outils derrière un proxy qui injecte les identifiants côté serveur, avec facturation à
l'appel. La question posée était : l'intégrer, ou en reprendre l'idée ?

## Décision

**On reprend l'idée, on n'ajoute pas la dépendance.** Choregos doit être une plateforme d'un
seul tenant, pas un assemblage de projets tiers dont chaque montée de version est un risque.

Le catalogue est donc à nous, et il repose entièrement sur des pièces qui existaient déjà :

| Besoin | Ce qui le porte |
|---|---|
| Déclarer des outils | Un fichier YAML apporté par le déploiement (ConfigMap), relu en PR |
| Détenir les clés | L'environnement de l'API, par référence à un Secret |
| Identifier l'appelant | Le **jeton de run**, déjà minté, déjà court, déjà vérifié |
| Exposer à l'agent | Le sidecar MCP `choregos-tools`, déjà câblé et déjà en localhost |
| Borner la dépense | `budgets.tool_calls_per_run`, comme les tours et les minutes |
| Compter | `cost_ledger`, avec une colonne `kind` qui distingue modèle et outil |

## Ce que cela règle, et que treg ne réglait pas

1. **Le jeton large.** Treg délivre un jeton par membre, valable pour *tous* les outils, sans
   plafond par clé. Dans un pod d'agent, c'est exactement ce qu'AGENTS.md interdit. Ici l'agent
   n'a aucun identifiant de fournisseur : il porte son jeton de run, qui ne vaut que pour son
   run et expire avec lui.
2. **Le trou de coût.** Un appel facturé hors de la plateforme est une dépense que personne ne
   voit ni ne plafonne. Chaque appel s'inscrit au registre avec son run, son projet et son
   étape — et le plafond refuse le suivant, sans discussion.
3. **L'egress de l'agent.** C'est la plateforme qui sort vers le fournisseur, pas le pod
   d'agent, dont la liste blanche reste fermée. Un agent ne peut pas joindre le fournisseur
   directement même s'il le voulait.

## Décision 2 — Un serveur MCP extérieur : sa clé, pas la nôtre

Un outil du catalogue peut venir d'un **serveur MCP extérieur à l'organisation** (`mcp:` au
lieu de `http:`). Trois règles, et chacune répond à une façon de se faire mal :

1. **Le jeton du run ne sort jamais.** Il authentifie l'agent *auprès de nous* : le donner à
   un service tiers reviendrait à lui confier de quoi écrire dans la plateforme — poster un
   résultat, déposer un finding, étendre un périmètre. Le serveur distant reçoit
   `credential_env`, une clé qui ne vaut que pour lui. Un test le vérifie en cherchant la
   valeur du jeton dans l'en-tête **et** dans le corps de la requête sortante.
2. **On n'expose qu'une partie d'un serveur, sous nos noms.** Le catalogue déclare le nom
   distant (`mcp.tool`) séparément du nom exposé. Cette indirection permet de ne publier que
   trois outils d'un serveur qui en offre soixante, et de les nommer dans le vocabulaire de
   la maison. L'agent ne voit jamais l'URL ni le nom distant.
3. **Chaque outil s'ouvre à des groupes.** `groups: [rh]` sur l'outil, `groups: [rh]` sur le
   projet. **Deux verrous, et ils ne disent pas la même chose** : le déploiement dit QUI a le
   droit, le projet dit ce dont IL se sert. Sans le premier, la liste du projet serait le
   seul contrôle — et elle est modifiable par l'équipe du projet elle-même. Un outil sans
   `groups` ne restreint rien : la restriction s'ajoute, elle n'est pas imposée.

Ce que cela ne fait pas : le catalogue n'interroge pas le serveur distant pour découvrir ses
outils. La liste est écrite, revue en PR, et ne change pas parce que le fournisseur a publié
une nouveauté. Un annuaire qui se met à jour tout seul n'est pas un contrôle d'accès.

## Ce qui est délibérément absent

- **L'URL n'est jamais choisie par l'agent.** Il remplit des gabarits `{{ champ }}` définis par
  le catalogue ; il ne fixe ni l'hôte, ni la méthode. Sans cette règle, le catalogue ferait de
  la plateforme un proxy ouvert appelant n'importe quoi avec ses propres clés — y compris un
  service de métadonnées d'instance.
- **Aucun outil par défaut.** Un projet déclare ce qu'il peut appeler ; la liste vide est le
  défaut, et un outil non déclaré rend **404**, pas 403 : un agent n'a pas à découvrir le
  catalogue du déploiement en essayant des noms.
- **Pas d'annuaire.** Le jour où l'on veut la largeur d'un catalogue public, c'est **un outil
  du catalogue** qui y mène, avec sa clé et son plafond. La plateforme ne dépend pas de lui.

## Conséquences

- `GET /internal/runs/{id}/tools` et `POST /internal/runs/{id}/tools/{name}`, au jeton de run.
- Le sidecar MCP mêle les outils du catalogue aux siens : l'agent les voit comme des outils
  ordinaires. Un catalogue injoignable ne le prive pas de `report_finding` et des autres.
- `global.toolCatalog` dans le chart : un ConfigMap pour le catalogue, un Secret pour les clés.
- `demo/outils/catalogue.yaml` contient **un** outil réel et sans clé — l'API Adresse de
  l'État — pour que la chaîne se montre sans demander de compte à personne.
- Une migration ajoute `cost_ledger.kind`, avec un `server_default` : l'autogénération
  produisait une colonne `NOT NULL` sans défaut, que PostgreSQL refuse sur une table peuplée.
