# Developing on Choregos

## Three levels, from the lightest to the closest to production

| Level | Command | What you get | What you need |
| :-- | :-- | :-- | :-- |
| demonstration | `make demo` | the whole chain in memory, one ticket to production | nothing |
| local services | `make compose-up` then `make api` / `make worker` | the real API and workers, simulated connectors | Docker |
| cluster | `make dev-up` | kind + Tekton + Argo + Tilt, hot reload | kind, kubectl, helm, tilt |
| bench | `make demo-up`, `make demo-images`, `make demo-seed` | real agents on a one-node kind, all dependencies embedded | Docker, kind, helm, an agent credential |

## Demonstration (no dependency)

```bash
make demo
```

A ticket enters, a simulated agent frames it, a human approves, the agent implements, gates
verify, the PR opens, the train deploys. The output shows the comment written to the ticket,
costs per stage and the finding turned into a linked ticket.

## Local services

```bash
make compose-up                          # Postgres, Temporal, LiteLLM, Keycloak, MinIO
CHOREGOS_FAKES=1 make api                # API on :8000
CHOREGOS_FAKES=1 make worker             # Temporal workers
make dev-seed                            # organisation, project, 10 tickets, one batch
pnpm -C apps/web dev                     # front on :3000
```

Development accounts (Keycloak, realm `choregos`): `augustin` / `choregos` (owner),
`marie` / `choregos` (release captain). Locally, `/auth/login?as=<email>` opens a session
without the IdP — **only** when `CHOREGOS_DEV_LOGIN_ENABLED=true` (the chart sets it through
`global.devLogin.enabled`, in `values/local.yaml` only), and `CHOREGOS_DEV_ADMIN_EMAILS`
names who becomes `org_admin`; everyone else is `developer`. The API refuses development
login in `staging` and `prod`. The front shows the development form when
`NEXT_PUBLIC_DEV_LOGIN=true`.

## Development cluster

```bash
make dev-up      # kind + dependencies + Tilt
make dev-seed
make dev-down    # destroy everything, volumes included
```

Tilt hot-reloads the API, the front, the workers, the runner and the sidecar, and exposes
three buttons: *seed*, *offline demo*, *tests*.

## Tests that touch the real world

Two families, outside the default suite. They are **skipped** when what they need is
missing — a test that does not run proves nothing, and saying so beats a green dot that
checked nothing.

### `tests/cluster` — what is only true on a real Kubernetes

```bash
make cluster-up                            # kind + Calico
make test-cluster                          # egress, Postgres restore, manifests, node loss
make cluster-down
```

The CNI must **enforce** NetworkPolicies. Neither kindnet nor Docker Desktop do: they accept
the policy and let traffic through. That is why `make cluster-up` installs Calico, and why
the first test of the suite checks this before anything else. `CHOREGOS_CLUSTER_CONTEXT`
selects the kube context (default `kind-choregos`).

### `tests/live` — adapters against the real services

```bash
CHOREGOS_LIVE_GITLAB_TOKEN=… CHOREGOS_LIVE_GITLAB_PROJECT=group/project \
CHOREGOS_LIVE_ECPHORIA_URL=http://127.0.0.1:8432 CHOREGOS_LIVE_ECPHORIA_TOKEN=… \
CHOREGOS_LIVE_LITELLM_URL=http://127.0.0.1:4000 CHOREGOS_LIVE_LITELLM_KEY=sk-… \
make test-live
```

No credential lives in the repository. These tests write for real: give them a sandbox
project, not one that matters. For LiteLLM a local proxy is enough:

```bash
docker run -d --name litellm -p 4000:4000 -v $(pwd)/dev/litellm.yaml:/app/config.yaml:ro \
  -e LITELLM_MASTER_KEY=sk-choregos-dev -e DATABASE_URL=postgresql://… \
  ghcr.io/berriai/litellm:main-stable --config /app/config.yaml --port 4000
```

**The database is mandatory**: without it LiteLLM cannot mint virtual keys, so no run
starts.

### Row-level security on a real PostgreSQL

`apps/api/tests/test_rls_postgres.py` and `test_evenements_postgres.py` only run against
PostgreSQL — SQLite has no RLS, and that is how the policy stayed fail-open for a week
without a test noticing. CI starts a PostgreSQL service; locally:

```bash
docker run -d --name choregos-test-pg -p 55433:5432 \
  -e POSTGRES_USER=choregos -e POSTGRES_PASSWORD=choregos -e POSTGRES_DB=choregos_test postgres:16-alpine
CHOREGOS_TEST_DATABASE_URL=postgresql+asyncpg://choregos:choregos@127.0.0.1:55433/choregos_test \
  uv run pytest apps/api/tests/test_rls_postgres.py apps/api/tests/test_evenements_postgres.py -q
```

The test migrates the schema with Alembic, then drops it: do not point it at a database that
matters. It creates a non-superuser role (`choregos_app`) for the application — a superuser
ignores RLS, so testing as one would prove nothing.

#### An embedded PostgreSQL volume created before 2026-09-24

The chart now creates the application role at initdb, and the API connects with it. An older
volume lacks the role: create it by hand, then transfer the schema to it:

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

## The front without the API

```bash
NEXT_PUBLIC_API_MODE=mock pnpm -C apps/web dev
```

Consistent fixtures tell the story of a living project: a ticket in progress, a pending
approval, a batch ready to depart, a finding to triage. The fixtures are loaded on demand and
never ship in a production bundle.

## Writing a workflow

```bash
choregos workflow templates                    # the shipped templates
choregos workflow validate .choregos/workflow.yaml
choregos workflow show .choregos/workflow.yaml --mermaid
```

Validation is **local and offline**: the same code as the orchestrator, so what you see is
what will apply. Errors carry a line and a column.

## Debugging a run

```bash
choregos items list <project>
choregos runs list <ticket-id>
choregos runs tail <run-id>      # ACP journal, live
choregos runs diff <run-id>      # annotated diff: out-of-scope changes are marked
```

In the front, a run's page shows its **guarantees** (each gate and its verdict), its
**evidence**, its **access card** and its journal.

## Known pitfalls

- **The Temporal test server downloads itself** on the first `pytest` touching workflows.
  Expect a connection, or run `uv run pytest packages` (no Temporal needed).
- **`CHOREGOS_FAKES=1` changes everything**: in fakes mode no real call is made and the
  displayed costs are simulated. Fine for developing, never for judging a model.
- **Migrations are N-1 compatible**: rolling back is safe, but a `downgrade` of a migration
  that drops a column loses data.
- **`make dev-up` publishes ports** (3000, 8000, 8080, 8088). If one is taken, kind fails at
  creation with `Bind for 0.0.0.0:3000 failed`. Free the port or edit the `hostPort`s in
  `dev/kind.yaml`.
- **An accepted NetworkPolicy is not an enforced NetworkPolicy.** On a cluster whose CNI does
  not enforce them, the runner sandbox holds nothing — and nothing says so. `tests/cluster`
  checks this first.
- **Open-source LiteLLM refuses key `tags`** (an Enterprise feature) and requires
  `key_alias`es unique for life. Both are handled by the adapter; do not add them back.
- **A helm upgrade during a run** used to kill the ticket (heartbeat timeout on a
  non-retried activity). `await_run` now retries; a workflow that dies for another reason
  writes why on the ticket, and the front shows it.
