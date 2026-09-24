# Développer sur Choregos

## Trois niveaux, du plus léger au plus proche de la production

| Niveau | Commande | Ce que ça donne | Ce qu'il faut |
| :-- | :-- | :-- | :-- |
| démonstration | `make demo` | la chaîne complète en mémoire, un ticket jusqu'à la prod | rien |
| services locaux | `make compose-up` puis `make api` / `make worker` | l'API et les workers réels, connecteurs simulés | Docker |
| cluster | `make dev-up` | kind + Tekton + Argo + Tilt, rechargement à chaud | kind, kubectl, helm, tilt |

## Démonstration (aucune dépendance)

```bash
make demo
```

Un ticket entre, un agent simulé cadre, un humain valide, l'agent implémente, les gates
vérifient, la PR s'ouvre, le train déploie. La sortie montre le commentaire écrit dans le
ticket, les coûts par étape et le finding transformé en ticket lié.

## Services locaux

```bash
make compose-up                          # Postgres, Temporal, LiteLLM, Keycloak, MinIO
CHOREGOS_FAKES=1 make api                # API sur :8000
CHOREGOS_FAKES=1 make worker             # workers Temporal
make dev-seed                            # organisation, projet, 10 tickets, un lot
pnpm -C apps/web dev                     # front sur :3000
```

Comptes de développement (Keycloak, realm `choregos`) : `augustin` / `choregos`
(propriétaire), `marie` / `choregos` (release captain). En local, `/auth/login?as=<email>`
ouvre une session sans passer par l'IdP — uniquement quand `dev_login_enabled` est vrai.

Depuis le 2026-09-24 cette porte est **fermée par défaut** et refusée en staging/prod :
`CHOREGOS_DEV_LOGIN_ENABLED=true` l'ouvre (le chart le fait avec `global.devLogin.enabled`,
`values/local.yaml` seulement), et `CHOREGOS_DEV_ADMIN_EMAILS` nomme les e-mails qui
reçoivent `org_admin` — les autres sont `developer`. Un e-mail qui commence par `admin`
n'est plus admin.

## Cluster de développement

```bash
make dev-up      # kind + dépendances + Tilt
make dev-seed
make dev-down    # tout détruire, volumes compris
```

Tilt recharge à chaud l'API, le front, les workers, le runner et le sidecar. L'interface
Tilt expose aussi trois boutons : *seed*, *démo hors ligne*, *tests*.

## Tests qui touchent le vrai monde

Deux familles, hors de la suite par défaut. Elles sont **ignorées** si ce dont elles ont
besoin manque — un test qui ne s'exécute pas ne prouve rien, et le dire vaut mieux qu'un
point vert qui n'a rien vérifié.

### `tests/cluster` — ce qui n'est vrai que sur un vrai Kubernetes

```bash
make cluster-up                            # kind + Calico
make test-cluster                          # S2-12 (egress), S7-04 (restauration), S8-06 (manifests)
make cluster-down
```

Le CNI doit **appliquer** les NetworkPolicy. Ni kindnet ni Docker Desktop ne le font : ils
acceptent la politique et laissent passer le trafic. C'est pour ça que `make cluster-up`
installe Calico, et que le premier test de la suite vérifie ce point avant tous les autres.

`CHOREGOS_CLUSTER_CONTEXT` choisit le contexte kube (défaut : `kind-choregos`).

### `tests/live` — les adaptateurs face aux vrais services

```bash
CHOREGOS_LIVE_GITLAB_TOKEN=… CHOREGOS_LIVE_GITLAB_PROJECT=groupe/projet \
CHOREGOS_LIVE_ECPHORIA_URL=http://127.0.0.1:8432 CHOREGOS_LIVE_ECPHORIA_TOKEN=… \
CHOREGOS_LIVE_LITELLM_URL=http://127.0.0.1:4000 CHOREGOS_LIVE_LITELLM_KEY=sk-… \
make test-live
```

Aucun identifiant n'est dans le dépôt. Ces tests écrivent pour de vrai : donnez-leur un
projet bac à sable, pas un projet qui compte.

Pour LiteLLM, un proxy local suffit :

```bash
docker run -d --name litellm -p 4000:4000 -v $(pwd)/dev/litellm.yaml:/app/config.yaml:ro \
  -e LITELLM_MASTER_KEY=sk-choregos-dev -e DATABASE_URL=postgresql://… \
  ghcr.io/berriai/litellm:main-stable --config /app/config.yaml --port 4000
```

La base est obligatoire : sans elle, LiteLLM ne peut pas créer de clé virtuelle, donc aucun
run ne démarre.

## La RLS sur un vrai PostgreSQL

`apps/api/tests/test_rls_postgres.py` ne tourne que contre PostgreSQL — SQLite n'a pas de
RLS, et c'est ainsi que la politique est restée fail-open une semaine sans qu'un test le
voie. La CI lance un service PostgreSQL ; en local :

```bash
docker run -d --name choregos-test-pg -p 55433:5432 \
  -e POSTGRES_USER=choregos -e POSTGRES_PASSWORD=choregos -e POSTGRES_DB=choregos_test postgres:16-alpine
CHOREGOS_TEST_DATABASE_URL=postgresql+asyncpg://choregos:choregos@127.0.0.1:55433/choregos_test \
  uv run pytest apps/api/tests/test_rls_postgres.py -q
```

Le test migre le schéma par Alembic, puis le détruit : ne pointez pas une base qui compte.

### Un volume PostgreSQL embarqué créé avant le 2026-09-24

Le chart crée désormais le rôle applicatif `choregos_app` (sans SUPERUSER) à l'initdb, et
l'API se connecte avec lui — un superutilisateur ignore la RLS. Un volume plus ancien n'a
pas ce rôle : le créer à la main, puis lui transférer le schéma.

```bash
kubectl -n choregos exec choregos-postgresql-0 -- sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "CREATE ROLE choregos_app LOGIN PASSWORD '"'"'$POSTGRES_PASSWORD'"'"' NOSUPERUSER" \
  -c "ALTER DATABASE choregos OWNER TO choregos_app" -c "ALTER SCHEMA public OWNER TO choregos_app" \
  -c "DO \$\$ DECLARE r record; BEGIN
        FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = '"'"'public'"'"' LOOP EXECUTE format('"'"'ALTER TABLE public.%I OWNER TO choregos_app'"'"', r.tablename); END LOOP;
        FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = '"'"'public'"'"' LOOP EXECUTE format('"'"'ALTER SEQUENCE public.%I OWNER TO choregos_app'"'"', r.sequencename); END LOOP;
        FOR r IN SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS args FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = '"'"'public'"'"' LOOP EXECUTE format('"'"'ALTER FUNCTION public.%I(%s) OWNER TO choregos_app'"'"', r.proname, r.args); END LOOP;
      END \$\$"'
```

## Le front sans l'API

```bash
NEXT_PUBLIC_API_MODE=mock pnpm -C apps/web dev
```

Des fixtures cohérentes racontent un projet vivant : un ticket en cours, une validation en
attente, un lot prêt à partir, un finding à trier.

## Écrire un workflow

```bash
choregos workflow templates                    # les trois modèles livrés
choregos workflow validate .choregos/workflow.yaml
choregos workflow show .choregos/workflow.yaml --mermaid
```

La validation est **locale et hors ligne** : le même code que l'orchestrateur, donc ce que
vous voyez est ce qui s'appliquera. Les erreurs portent la ligne et la colonne.

## Déboguer un run

```bash
choregos items list <projet>
choregos runs list <ticket-id>
choregos runs tail <run-id>      # journal ACP en direct
choregos runs diff <run-id>      # diff annoté : ce qui est hors périmètre est marqué
```

## Pièges connus

- **Le serveur de test Temporal se télécharge** au premier `pytest` sur les workflows.
  Prévoir une connexion, ou lancer `uv run pytest packages` (aucun Temporal requis).
- **`CHOREGOS_FAKES=1` change tout** : en mode fakes, aucun appel réel n'est fait, et les
  coûts affichés sont simulés. C'est très bien pour développer, jamais pour juger un modèle.
- **Les migrations sont compatibles N-1** : revenir en arrière est sûr, mais un `downgrade`
  d'une migration qui supprime une colonne perd des données.
- **`make dev-up` publie des ports** (3000, 8000, 8080, 8088). Si l'un est déjà pris sur la
  machine, kind échoue à la création avec `Bind for 0.0.0.0:3000 failed`. Libérez le port ou
  éditez les `hostPort` de `dev/kind.yaml`.
- **Une NetworkPolicy acceptée n'est pas une NetworkPolicy appliquée.** Sur un cluster sans
  CNI qui les fait respecter, le bac à sable des runners ne retient rien — et rien ne le
  signale. `tests/cluster` commence par vérifier ce point.
- **LiteLLM open-source refuse les `tags` de clé** (fonction Enterprise) et exige des
  `key_alias` uniques à vie. Les deux sont gérés par l'adaptateur ; ne les remettez pas.
