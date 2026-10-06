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
make compose-up                          # Postgres, Temporal, LiteLLM, Keycloak, RustFS (S3)
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

## Shipping a workflow template from outside this tree

Set `CHOREGOS_WORKFLOW_TEMPLATES_DIR` to one or more directories (separated by `:`) holding
`<name>.yaml` workflows. They appear in `template_names()`, in `choregos workflow templates`, and
can be loaded by name like the shipped ones.

A deployment template **wins over a shipped one of the same name**, which is how a shop adapts
`default-simple` without rewriting the platform. Same seam as `CHOREGOS_PLAYBOOKS_DIR`, and for
the same reason: a domain's states and transitions do not belong in this package, while budgets,
gates and memory do not change from one domain to the next.

Before this existed, the only way in was `PUT /projects/{id}/workflow` with the full YAML — no
name, no reuse, and no way for two projects to start from the same model.

## Shipping a gate from outside this tree

A plugin can register a gate the same way it registers a connector, and nothing else is needed:

```python
from choregos_core.gates import GateContext, GateOutcome, gate

@gate("collecteur_propre")
def _collecteur_propre(ctx: GateContext, params: dict) -> GateOutcome:
    faits = (ctx.result.evidence.facts or {}) if ctx.result else {}
    propre = bool(faits.get("collecteur_propre"))
    return GateOutcome("collecteur_propre", propre, detail="…")
```

The property that decides everything is that the **DSL validator accepts it**: an unknown gate is a
blocking error (`gate.unknown`), so a gate registered at start-up must become known to the
validator, or no workflow using it would ever be valid. It does, and
`packages/core/tests/test_gate_hors_de_l_arbre.py` proves it — including that the gate really is
missing before the plugin loads, so the test cannot lie to itself.

No field needs to be added to `GateContext`: `ctx.result` already carries the step's outputs and
`evidence.facts`. That is enough for any gate that judges what the step produced — which is most of
them.

**What still needs a core change**: a gate that reads something the context does not carry — a live
diff, a CI verdict, the cost ledger — needs its field on `GateContext` *and* its population in
`activities/gates.py`. That is a narrower limit than the one the plan assumed, and it is worth
knowing before designing around it.

## Shipping a connector from outside this tree

A package installed next to Choregos can register its own connectors — no fork, no patch to the
core. Declare an entry point in the `choregos.plugins` group:

```toml
# pyproject.toml of your package
[project.entry-points."choregos.plugins"]
mon-tracker = "mon_paquet:brancher"
```

```python
# mon_paquet/__init__.py
from choregos_adapters import register

def brancher() -> None:
    register("tracker", "maison")(lambda cfg: MonTracker(cfg))
```

The API and the orchestrator load the group at start-up. A declared plugin that fails to load
**stops the process**, naming it: someone installed it so that it would serve, and a silently
missing plugin leaves a platform that looks complete and is not. Same rule as `garde.yaml`.

A plugin can also **serve routes**. It builds an ordinary `APIRouter`, with the core's own
dependencies for authentication and rights (`Me`, `Db`…), and declares it:

```python
from fastapi import APIRouter
from choregos_api.greffons import declarer_un_routeur

routeur = APIRouter(tags=["mine"])

@routeur.get("/mine/ping")
async def ping() -> dict[str, str]:
    return {"pong": "mine"}

def brancher() -> None:
    declarer_un_routeur(routeur)   # served under /api/v1, after the core's routers
```

A plugin **adds** routes; it never replaces one. A route that covers a core route (same method,
same path) **stops start-up**, naming it: Starlette serves the first match, so the plugin would be
silently ignored — or, if the order ever changed, would silently replace a core route, an
authentication route included. Nested routers are refused for the same reason: FastAPI exposes them
only through a private object, and a route nobody can see is a route nobody can check.
`apps/api/tests/test_greffons_routes.py` proves both with a plugin installed for real.

A plugin that serves administration routes gives them **screens without shipping code to the
console** ([ADR 0032](adr/0032-sections-d-administration-par-manifeste.md)): it declares each
section as data — `form` (a JSON Schema, a read path, a write path), `table` (a list path, columns,
row actions), `action` (a confirmation, `danger`, `reauth`), `secret_once` (a secret shown once,
never kept) — and the console renders it under `/admin/x/{section}`:

```python
from choregos_api.greffons import declarer_une_section_d_administration

def brancher() -> None:
    declarer_un_routeur(routeur)
    declarer_une_section_d_administration({
        "id": "scim", "title": "SCIM provisioning", "scope": "organisation",
        "permission": "member:manage",
        "blocks": [{"kind": "table", "title": "tokens", "list": "/orgs/{org}/scim/tokens",
                    "columns": [{"key": "name", "label": "name"}]}],
    })
```

Paths are relative to `/api/v1`; `{org}` is the current organisation, any other `{…}` a row's
key. At start-up the core validates every manifest against `ui-manifest.schema.json`, checks the
permission exists, and checks that **every path it names is served** by a route with that method —
otherwise start-up stops, naming the section. Declaring the same manifest twice is harmless (the API
loads plugins twice: through the orchestrator it imports, then in `create_app()`); another manifest
under the same `id` stops start-up. `GET /api/v1/ui/admin-sections` returns the sections the caller
holds the permission for; a section asking for `platform:admin` goes to platform administrators
only, whatever its scope — an `org_admin` holds every permission in their own organisation.
`apps/api/tests/test_sections_d_administration.py` proves it.

A `form` block reads its `read` path and writes back, to `write`, **only the fields of its schema**:
give it exactly the fields of the model its route writes, or a forgotten one goes back to its default
on every save. The console draws a field from its JSON Schema: `string` (with `format: date`,
`password` or `multiline`, the last for a PEM certificate), `number`, `integer`, `boolean`, `enum`,
an `array` of strings (comma separated) and an `object` of strings (one `key = value` per line).

This is how the enterprise edition attaches ([ADR 0024](adr/0024-deux-editions.md)), and it is
the second seam of this kind after playbooks (`CHOREGOS_PLAYBOOKS_DIR`), which was the model.
`packages/adapters/tests/test_greffons.py` proves it by writing a real `.dist-info` on disk
rather than stubbing the discovery.

A plugin also brings the **effects** a governed action can run
([ADR 0035](adr/0035-actions-gouvernees-dans-le-coeur.md)):

```python
from choregos_api.effets import EffetRefuse, declarer_un_effet

async def activer_badge(ctx, params):          # ctx: session, action, project
    badge = await lecteur.activer(params["uid"])   # idempotent: activating twice is one badge
    if badge.refuse:
        raise EffetRefuse("badge unknown to the reader")  # final: no retry, the action compensates
    return {"uid": badge.uid, "active": True}      # recorded under the effect's key

def brancher() -> None:
    declarer_un_effet("badge.activate", activer_badge, politique="approval")
```

`politique` is what a **workflow** action needs to run this effect: `approval` (a person decides,
the default), `allowed` (the transition is enough) or `forbidden`. `connector.call` has none of its
own: the operation it calls carries the policy, in the organisation and in the project.

An effect runs in an activity of `ActionWorkflow`, never in the request that approved the action.
It **must be idempotent**: a worker may die after the call and before the confirmation, and the
effect is then attempted again. Raise `EffetRefuse` for what no retry will change; any other
exception is retried with backoff. Declaring the same function twice is harmless; another under
the same name stops start-up.

A connector of a **business family** — `mdm`, `shipping`, `access_control` — declares that
family's operations (`choregos_adapters.familles`) and enters the shared conformance suite,
`tests/conformance/connecteurs/`, with the fake of its API: a case of a few lines, and it is judged
like the demo fakes, the Entra directory and the supplier's MCP server. Each operation declares a
**closed** input schema that the implementation follows — same parameters, same types, required
exactly when they have no default; a **write replayed changes nothing**, and a read writes nothing,
not even for an object it does not know; the key **serves** — a wrong one is refused — and
**appears nowhere**: not in the log (stdlib or structlog), not in a result, not in a refusal, not
in the client's `repr`. The fakes of the HR scenario (`choregos_adapters.fakes.rh`) can be reached
typed, through a `demo` connector, or served as an MCP server (`serveur_mcp`), on the same state.

### Adding a plugin to a published image

A plugin that ships as an image is built **FROM** the community image, which already holds the
core. The wheel declares no dependency on the core (its wheels are GitHub release artefacts, not
on PyPI) and goes in with `--no-deps`:

```dockerfile
FROM ghcr.io/vargafoundation/choregos-api:<version>
COPY choregos_ee-*.whl /tmp/
RUN pip install --no-deps --no-index /tmp/choregos_ee-*.whl
```

That obvious gesture is correct **only since the images set `PIP_PYTHON`**. The virtualenv in
`/app/.venv` is created by uv and has no `pip`; the one on the `PATH` belongs to the system. On
`choregos-api:0.8.3` and earlier the build succeeds, the wheel lands in `~/.local`, outside the
virtualenv, and the platform runs as `community` without a word — only the start-up guard catches
it, and only if `global.edition: enterprise` was set. On those images, write
`pip --python /app/.venv/bin/python install …`. `tests/paquets/test_image_ee.py` builds a real
image FROM a community one and checks the edition the platform reports
(`CHOREGOS_IMAGES_CE=choregos-api:local,choregos-orchestrator:local`).
## Shipping a schema from a plugin

The migrations live **inside** the `choregos_api` package and are run by one command, the one the
chart's migration Job runs:

```sh
python -m choregos_api.migrer            # upgrade heads — the core, then every plugin branch
python -m choregos_api.migrer downgrade <plugin_label>@base
```

A plugin that needs tables declares a directory of Alembic revisions in the `choregos.migrations`
entry-point group; the value it points to is a path:

```toml
[project.entry-points."choregos.migrations"]
mine = "mon_paquet:EMPLACEMENT_DES_MIGRATIONS"   # pathlib.Path to a versions directory
```

Its revisions form a **branch** in the same `alembic_version` table: the first one has
`down_revision = None`, a `branch_labels` of its own and `depends_on` a core revision. A plugin
creates **its** tables and may point foreign keys at the core's; it never alters a core table —
two owners for one table is how the next core migration breaks. A declared directory that does not
exist stops the command: a plugin whose schema silently did not run gives a platform that starts
and fails at the first request touching its tables. `apps/api/tests/test_migrer.py` proves all of
it with a plugin installed for real.
## Refusing a run from a plugin

The core caps spending per run and concurrency per executor. A rule that depends on something
else — a suspended organisation, a monthly ceiling, a freeze window — is declared by a plugin:

```python
from choregos_core.admission import AdmissionRefusee, declarer_une_admission

async def organisation_active(demande):          # a DemandeDeRun: org, project, work_item, budget_usd…
    if await est_suspendue(demande.org):
        raise AdmissionRefusee(f"{demande.org} is suspended — reactivate it, then relaunch")

def brancher() -> None:
    declarer_une_admission("suspension", organisation_active)
```

The orchestrator plays every declared check in `prepare_stage`, **before** minting the gateway key
and creating the run, so a refusal costs nothing and leaves no key behind — and **after** the replay
path, so a run already prepared is never refused afterwards (what is running finishes). A refusal
stops the stage without retry and marks the ticket dead with the reason, prefixed by the check's
name: write it so it says what to do. A check that raises anything else is an outage, not a
decision; it propagates and Temporal retries. `apps/orchestrator/tests/test_admission.py` proves it
with a plugin installed for real.

## Identity seams: revoking a session, granting platform administration

Three hooks in `choregos_api.edition`, next to the group mapper:

- **`declarer_une_validation_de_session(fn)`** — `fn(session_db, user, payload)`, sync or async,
  runs on every cookie-authenticated request; raise `SessionRefusee(reason)` to answer 401 with
  that reason. A signed session is otherwise valid until `exp`: this is how server-side revocation
  and SCIM deprovisioning cut access at the next request. Any other exception propagates — a
  validation that is down is not an acceptance.
- **`declarer_un_administrateur_de_plateforme(fn)`** — `fn(session_db, principal) -> bool`. The
  core's rule (org admin of *every* organisation) still applies; a plugin can only **grant** on top
  of it, never take away.
- The session now carries **`iat`**, surfaced as `Principal.authentifie_le`, and
  `/api/v1/auth/login?reauth=1` asks the IdP to authenticate again (`prompt=login`, `max_age=0`) —
  the path for a gate that requires a fresh authentication. Sessions issued before 0.10.0 have no
  `iat` and read as too old.

`apps/api/tests/test_coutures_identite.py` proves each with a plugin installed for real.

## Refusing a human gesture from a plugin

Three routes consult plugin controls **after** the core's rights and **before** acting:
`project.create`, `workitem.decision` and `release.approve`.

```python
from choregos_api.greffons import GesteRefuse, declarer_un_controle_de_geste

def fraicheur(session, demande):           # DemandeDeGeste: geste, org, principal, cible
    quand = demande.principal.authentifie_le
    if quand is None or time.time() - quand > 300:
        raise GesteRefuse("approving needs a login less than 5 minutes old", "reauth")

def brancher() -> None:
    declarer_un_controle_de_geste("release.approve", "fraicheur", fraicheur)
```

The nature picks the answer: `interdit` → 403, `conflit` → 409, `reauth` → 401 pointing at
`/api/v1/auth/login?reauth=1`. A control can only refuse more than the core; an exception other
than `GesteRefuse` propagates. `apps/api/tests/test_controle_des_gestes.py` proves the three routes.

## Running the API suite on PostgreSQL

SQLite has no row-level security. The API suite therefore runs twice in CI: on SQLite, then on
PostgreSQL with the schema migrated by `python -m choregos_api.migrer` and a **non-superuser** role
(a superuser ignores RLS whatever the policies say):

```sh
docker run -d --name pg -e POSTGRES_USER=choregos -e POSTGRES_PASSWORD=choregos \
  -e POSTGRES_DB=choregos_test -p 5432:5432 postgres:16-alpine
CHOREGOS_TEST_DATABASE_URL=postgresql+asyncpg://choregos:choregos@localhost:5432/choregos_test \
CHOREGOS_TEST_SUITE_SUR_POSTGRES=1 uv run pytest apps/api/tests
```

In that mode, a test's bare `session_scope()` — a fixture setting up data — means "as the platform".
Product code never opens an unscoped session, so this hides nothing; `test_rls_postgres.py` keeps the
real fail-closed behaviour. Three defects only showed there (webhooks routing nothing, `POST /orgs`
answering 500, a project role becoming an organisation role).

## Setting a project's namespace quota from a plugin

Each project gets its namespaces and a `ResourceQuota`. The default is 32 CPU, 96 Gi, 40 pods; a
plugin can set it per organisation:

```python
from choregos_core.quotas import Quota, declarer_un_quota

async def quota(org: str, projet: str) -> Quota | None:
    return Quota(cpu="8", memoire="16Gi", pods=10) if org == "small" else None   # None: default

def brancher() -> None:
    declarer_un_quota(quota)
```

It is written to the GitOps repository at provisioning, so it takes effect the next time the project
is provisioned. A malformed quantity is refused at construction, not by Argo CD later. Why there is no
Temporal queue per organisation: [ADR 0027](adr/0027-le-voisinage-se-regle-a-l-admission-et-au-quota.md).

## Known pitfalls

**`CHOREGOS_FAKES=1` makes `build()` return a fake, whatever type you asked for.** The API test
suite sets it globally, so a test that builds a real adapter passes on its own and fails in the
group. Set `CHOREGOS_FAKES=0` explicitly in such a test. This has cost three debugging sessions;
it is written here so it costs no more.

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
