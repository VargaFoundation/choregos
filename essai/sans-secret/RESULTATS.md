# Essai, élément 8 — résultats du 2026-10-03

**Question** : un run peut-il recevoir son jeton et ses secrets **sans aucun Secret Kubernetes**, quand
l'orchestrateur n'a aucun droit sur les Secrets du namespace d'exécution (mécanisme (a) de l'ADR 0005
du cahier des charges) ? Et l'amorçage tient-il dans un binaire statique, dans une image sans Python
(ADR 0015) ?

**Réponse : oui aux deux, avec une correction de contrat (le cas « pas encore prêt »).**

## Montage

- Cluster kind (`kindest/node:v1.36.1`), deux namespaces en **PSA `restricted`** (`essai-control`,
  `essai-run`).
- L'orchestrateur, dans `essai-control`, a une Role dans `essai-run` limitée à `jobs` (créer, lire,
  supprimer), `pods` (lire, surveiller) et `pods/log` : **aucun verbe sur les Secrets**.
- Le Job du run porte en variable d'environnement un **code d'amorçage à usage unique** (32 octets
  aléatoires), jamais un Secret. Le pod tourne sans jeton de compte de service, en non-root, système de
  fichiers en lecture seule, toutes capacités retirées.
- Au démarrage, l'amorçage (`runtime/`, Rust, **binaire statique de 870 Ko dans busybox**) échange le
  code contre un jeton de run et ses secrets déclarés, lance la commande avec les secrets en mémoire,
  puis publie le résultat avec son jeton.
- Le bouchon d'API (`api/app.py`) applique les règles de 03 §12.1 ; l'orchestrateur relève l'adresse
  du pod (surveillance des pods) et la lui transmet : l'API n'a aucun droit Kubernetes.

## Ce qui a été prouvé

| # | Contrôle | Résultat |
|---|---|---|
| 1 | Le run reçoit son secret (32 caractères) en mémoire et réussit ; son résultat est accepté avec son jeton | ✅ |
| 2 | Un **rejeu** du code consommé rend **409**, met le run en échec (`bootstrap_replayed`) et révoque le jeton | ✅ |
| 3 | Un code forgé rend **401** | ✅ |
| 4 | Un code dont le pod a démarré il y a plus de 10 minutes rend **410** | ✅ |
| 5 | Un code présenté depuis une **autre adresse** que celle du pod rend **403** | ✅ |
| 6 | L'orchestrateur ne peut ni créer ni lire de Secret dans `essai-run` ; **0 Secret** dans ce namespace | ✅ |

Rejouer : `./run.sh` (environ 1 minute une fois le cluster créé ; 5 minutes avec la création).

## La correction de contrat trouvée par l'essai

**Le pod appelle l'amorçage avant que l'orchestrateur ait relevé son adresse.** Au premier passage,
l'API répondait « code expiré » (pas d'heure de démarrage connue), et le run échouait. Il faut un
cas **retentable** :
- l'API rend **425 `bootstrap_not_ready`** (avec `Retry-After`) tant que l'adresse et l'heure de
  démarrage du pod ne sont pas enregistrées ;
- l'amorçage réessaie, au plus 60 fois à une seconde d'intervalle ; au second passage de l'essai, il a
  réussi à la deuxième tentative.

À reporter dans 03 §12.1, 10 §5.5, l'ADR 0005 et SOC-093.

## Autres constats

- **kind 0.30 ne charge plus les images dans un nœud 1.36** (`unknown containerd config version: 4`).
  Contournement utilisé : `docker save … | docker exec -i <nœud> ctr --namespace=k8s.io images import -`.
  À reprendre dans les tests de cluster (SOC-122) tant que kind ne suit pas.
- La source de l'adresse vue par l'API est bien l'adresse du pod pour un appel pod → Service dans le
  cluster (kube-proxy en iptables ne fait pas de SNAT ici). Derrière un proxy ou un maillage de services,
  il faut l'en-tête de confiance (`KOINON_TRUSTED_PROXY_HEADER`) : non éprouvé par l'essai.
- Le code reste lisible dans la spécification du pod par qui peut lire les pods du namespace : c'est le
  prix connu du mécanisme (a), borné par l'usage unique, la liaison à l'adresse et la validité de
  10 minutes. Le rejeu est détecté et fait échouer le run (contrôle 2).
