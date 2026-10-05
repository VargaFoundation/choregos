# Essai, élément 9 — démarrage local : résultats du 2026-10-04

**Question** : la pile de l'essai démarre-t-elle par `docker compose up` sur un portable, en
**10 minutes au plus et 6 Go au plus** ? Et la tranche IT4IT (éléments 1 à 6) tient-elle contre cette
pile qui tourne, et pas seulement dans les tests ?

**Réponse : oui aux deux, sur une machine de développement aux images en cache.** Le temps d'un
poste neuf dépend du téléchargement (environ 1,5 Go d'images). Et la pile qui tourne a trouvé un
défaut du cœur, invisible aux tests : la transaction d'une requête était validée **après** l'envoi de
la réponse.

## La pile intégrée (`compose.yaml`, `run.sh`)

- **PostgreSQL** (`postgres:16-alpine`) avec un **rôle applicatif non superutilisateur**
  (`initdb/`) : migrations et API tournent sous ce rôle, donc sous RLS forcée ;
- **migrations** par `python -m choregos_api.migrer`, la commande du Job du chart : le cœur, puis
  la branche `ontology` du greffon ;
- **API** (uvicorn) et **orchestrateur** (worker Temporal), images construites depuis
  `docker/api.Dockerfile`, plus une couche de 238 octets qui déclare le greffon comme le ferait
  `pip install` (`greffon.Dockerfile`) ;
- **Temporal** (`auto-setup:1.26.2`) ;
- le **lakehouse** de l'élément 7 (Lakekeeper, sa base, et RustFS à la place de MinIO, Q22), inclus
  tel quel ;
- la paire de clés des jetons de run, générée par `run.sh` et partagée par l'API, l'orchestrateur
  et le décor.

## Mesures (machine de développement, WSL2, images de base en cache)

| Mesure | Résultat |
|---|---|
| Construction des images de la plateforme (cache uv chaud) | 27 à 59 s |
| Démarrage (`docker compose up -d --wait`) jusqu'à l'API saine | **18 à 21 s** |
| Mémoire au repos, toute la pile (quatre passages) | **572 à 854 Mio** (Temporal 73–202 ; orchestrateur 112–147 ; PostgreSQL 145–163 ; API 97–137 ; stockage S3 74–157). Avec RustFS : 812 Mio |
| Images à télécharger sur un poste neuf | environ 1,6 Go décompressés (Temporal 426 Mo, API et orchestrateur 321 Mo à eux deux, PostgreSQL 294 Mo, RustFS 290 Mo, Lakekeeper 176 Mo, mc 85 Mo) ; toutes publiées, sauf celles de la plateforme, construites par `run.sh` |

La mesure de référence du 2026-10-03 portait sur la pile de développement de Choregos : 3,3 Go
d'images et 1,8 Gio, à cause de LiteLLM et de Keycloak, dont le mode local de l'essai se passe.

## Le scénario contre la pile qui tourne (`scenario.py`)

| Étape | Résultat |
|---|---|
| Connexion de l'administrateur (connexion de développement), projet, connecteur SCM | ✅ |
| Élément 1 : le paquet IT4IT est validé, compilé, actif (13 outils) | ✅ |
| Élément 2 : synchronisation ; rejeu sans écriture ; rapport tronqué refusé (422) sans écriture | ✅ |
| Élément 3 : avec le jeton du run, l'agent liste les outils, lit les constats ouverts et un hôte sans son adresse | ✅ |
| Élément 4 : proposition `pending_approval`, avec justification | ✅ |
| Élément 5 : validation après `?reauth=1` ; PR ouverte par la plateforme sur `choregos/<proposition>` ; décision consignée avec `auth_time` | ✅ |
| Élément 6 : la relance du collecteur sans la clé rend la vérification `succeeded` ; fait `collector_clean` à `true` | ✅ |
| En base : RLS forcée sur les trois tables du greffon ; rôle applicatif non superutilisateur | ✅ |

Rejouer : `essai/demarrage-local/run.sh` (environ 2 minutes ; `GARDER=1` laisse la pile debout).

## Le défaut trouvé : le `commit` après la réponse

Premier passage : la connexion réussit, la requête suivante répond **401 « session périmée »**.
FastAPI ferme par défaut les dépendances `yield` **après** l'envoi de la réponse ; `get_db` y validait
sa transaction. Le client recevait sa redirection, puis la base écrivait l'utilisateur : une requête
rapide arrivait avant. Mesuré sur la pile : **6 connexions neuves sur 10 échouent** sans attente,
aucune avec une demi-seconde.

La conséquence la plus grave n'est pas la course : un `commit` qui échoue après coup laisse au client
une réponse de succès sur une écriture perdue. Les tests ne le voyaient pas, parce que
`ASGITransport` attend la fin de l'application avant de rendre la réponse.

Correctif dans la branche : `Depends(get_db, scope="function")`, qui valide la transaction avant
l'envoi. Le test `apps/api/tests/test_commit_avant_la_reponse.py` appelle l'application ASGI
directement et vérifie l'ordre des événements ; sans le correctif, il échoue. Issue `finding`
ouverte pour la ligne principale.

## Ce que l'élément 9 ne prouve pas

- **Le temps sur un poste neuf** : environ 1,6 Go à télécharger, soit 2 à 3 minutes à 100 Mbit/s et
  le double à 50 Mbit/s, sous l'objectif de 10 minutes ; non mesuré sur une vraie connexion lente. Le
  blocage de MinIO est levé : RustFS le remplace (élément 7, section Q22), et la pile ne dépend plus
  d'aucune image en cache.
- **Le mode local sans connexion de développement** : l'authentification passe par `?as=` ; le mode
  local de la spec (mot de passe, ADR 0011) n'existe pas dans le cœur.
- **L'agent, le collecteur et le SCM** : le scénario écrit les appels de l'agent, poste les rapports
  du collecteur, et la PR s'ouvre sur le faux SCM du cœur, dans le processus de l'API.
