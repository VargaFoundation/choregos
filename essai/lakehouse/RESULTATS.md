# Essai, élément 7 — résultats du 2026-10-03

**Question** : un catalogue Iceberg REST standard (Lakekeeper) sur un stockage S3 (MinIO), écrit par
PyIceberg et lu par DuckDB, tient-il les ADR 0007 (Lakekeeper) et 0008 (DuckDB en local) du cahier des
charges, avec des identifiants de stockage limités à ce que le run doit toucher ?

**Réponse : oui, en mode STS.**

## Versions

| Composant | Version |
|---|---|
| Lakekeeper | 0.13.6 (`quay.io/lakekeeper/catalog:latest`) |
| MinIO | RELEASE.2025-09-07T16-13-09Z (image en cache, voir « risques ») |
| PyIceberg | 0.10.x, avec `pyarrow` 25.0.1 et `s3fs` |
| DuckDB | 1.5.6, extensions `iceberg` et `httpfs` |
| PostgreSQL (base de Lakekeeper) | 16 |

## Ce qui a été prouvé

| # | Preuve | Mesure |
|---|---|---|
| 1 | PyIceberg crée l'espace de noms imbriqué `p_demo.health` (règle de 03 §2.2) et une table, puis y écrit 1 000 lignes | 2,0 s en signature distante ; 0,39 s en STS |
| 2 | DuckDB attache le catalogue REST et relit les bons comptes (1 000 lignes, 50 clés, 667 constats), espace de noms imbriqué compris | 33 à 39 ms |
| 3 | **En STS, aucun secret S3 côté client** : le catalogue délivre des identifiants temporaires (jeton de session), utilisés par PyIceberg et par DuckDB (`ACCESS_DELEGATION_MODE 'vended_credentials'`) | — |
| 4 | **Ces identifiants n'ouvrent que la table** : lecture et écriture dans son emplacement permises ; liste de la racine du bucket, écriture sous un autre préfixe et lecture d'un autre bucket refusées (`ACCESS_DENIED`) | `portee.py` |
| 5 | Empreinte de la pile au repos | Lakekeeper 55 Mio, PostgreSQL 92 Mio, MinIO 193 Mio |

Rejouer : `./run.sh`, puis `STS=true WAREHOUSE=essai-sts DELEGATION=vended ./run.sh` (environ 75 s,
installation des paquets Python comprise).

## Ce qu'on en tire pour le cahier des charges

- **ADR 0007 confirmée** : Lakekeeper sert de catalogue sans développement, et sa délivrance
  d'identifiants par table donne l'isolation que la façade maison de graal construisait à la main. Les
  gardes de commit de graal deviennent des cas de test (R-DAT-LAK-04).
- **ADR 0008 confirmée pour la lecture** : DuckDB lit Iceberg par le catalogue REST, espaces de noms
  imbriqués compris.
- **Le mode STS est obligatoire** dès que DuckDB lit : DuckDB ne sait pas faire la signature distante
  (il lui faudrait sinon ses propres identifiants S3, que le run ne doit pas tenir). Le profil de
  stockage de Lakekeeper doit donc activer STS (`sts-enabled`, avec un `sts-role-arn` même factice
  pour MinIO). R-DAT-LAK-03 : « STS d'abord, signature distante en repli pour les moteurs qui la
  savent ».
- **L'emplacement des tables est un UUID** (`warehouse-sts/<uuid>`), pas le chemin de l'espace de
  noms : la règle « tout emplacement sous le préfixe du projet » (R-DAT-LAK-04) passe par l'option
  `storage-layout` de Lakekeeper, ou devient « toute table sous le bucket ou le préfixe de
  l'entrepôt du projet ». À trancher dans DAT-003.

## Risques relevés

- **L'image MinIO n'est plus publiée**, ni sur Docker Hub ni sur quay.io (vérifié le 2026-10-04).
  Le démarrage local par `docker compose up` (D8) ne peut pas en dépendre : voir la section suivante,
  qui la remplace par RustFS.
- Deux entrepôts ne peuvent pas partager un même bucket sans préfixe (`CreateWarehouseStorageProfileOverlap`) :
  un entrepôt par installation (ADR 0007) le permet, avec un préfixe par projet si besoin.
- Lakekeeper tournait **sans authentification** dans l'essai. En production, il se branche sur l'IdP
  (OIDC) et sur un autorisateur (OpenFGA ou équivalent) ; c'est l'objet de DAT-001 et DAT-003.

## Q22 : RustFS à la place de MinIO (2026-10-04)

**Question** : quel stockage S3 remplace MinIO, dont l'image n'est plus publiée, sans perdre ce que
l'élément 7 a montré nécessaire, des identifiants STS bornés par table ?

**Réponse : RustFS** (`rustfs/rustfs:1.0.1`, Apache-2.0, image publiée). Désormais le stockage par
défaut de cette pile (service `s3`).

| Contrôle | Résultat |
|---|---|
| `AssumeRole` avec une politique de session limitée à `projet-a/*` | identifiants temporaires délivrés |
| Lire `projet-a` avec ces identifiants | permis |
| Lire ou écrire `projet-b` avec ces identifiants | refusé (`AccessDenied`) |
| L'élément 7 complet en mode STS : écriture PyIceberg, lecture DuckDB par identifiants délivrés | 1 000 lignes écrites et relues |
| Test de portée (`portee.py`) : sa table permise ; la racine du bucket, un autre préfixe, un autre bucket refusés | identique à MinIO |

Écartés sans essai : SeaweedFS (pas de STS) ; Garage et une image MinIO construite depuis les sources
(AGPL-3.0, incompatible avec un rapport de licences sans AGPL).

Ce qui n'est pas prouvé : la tenue de RustFS en charge et dans la durée (projet jeune, 1.0 en 2026),
la réplication et la reprise. Le client `mc` sert encore à créer les buckets : il est publié, mais
c'est un outil de MinIO (AGPL), à remplacer par un client S3 générique dans la pile livrée.
