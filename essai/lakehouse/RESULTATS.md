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

- **L'image MinIO n'est plus publiée sur Docker Hub** : `docker pull minio/minio` est refusé le
  2026-10-03 ; l'essai a utilisé une copie en cache (septembre 2025). Le démarrage local par
  `docker compose up` (D8) ne peut pas en dépendre. Candidats à éprouver : SeaweedFS (déjà prévu par
  DAT-003, sans STS), Garage, ou une image MinIO construite depuis les sources. **À trancher au J0.**
- Deux entrepôts ne peuvent pas partager un même bucket sans préfixe (`CreateWarehouseStorageProfileOverlap`) :
  un entrepôt par installation (ADR 0007) le permet, avec un préfixe par projet si besoin.
- Lakekeeper tournait **sans authentification** dans l'essai. En production, il se branche sur l'IdP
  (OIDC) et sur un autorisateur (OpenFGA ou équivalent) ; c'est l'objet de DAT-001 et DAT-003.
