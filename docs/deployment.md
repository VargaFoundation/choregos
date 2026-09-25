# Deployment

Choregos ships as a Helm umbrella chart with four workloads — API, web, Temporal workers,
and an optional MCP tools sidecar — plus four dependencies it can either **embed** or
**consume**.

## Prerequisites

| | Minimum | Notes |
| :-- | :-- | :-- |
| Kubernetes | 1.24 | `spec.suspend` on Jobs (GA in 1.24) is used to queue runs |
| Helm | 3.12 | The chart is also published as an OCI artifact |
| PostgreSQL | 15 | Embedded single instance for a bench, an operator in production |
| Temporal | 1.22 | Embedded dev server for a bench, a cluster in production |
| An OIDC provider | — | Keycloak in our deployments; any OIDC issuer works |

Optional but recommended in production: a model gateway (LiteLLM), a memory service
(Ecphoria), Tekton for CI, Argo CD for CD, gVisor as a `RuntimeClass` for agent sandboxes.

## Install

```bash
helm upgrade --install choregos oci://ghcr.io/vargafoundation/charts/choregos \
  --version 0.2.0 --namespace choregos --create-namespace \
  -f my-values.yaml
```

Images are published under `ghcr.io/vargafoundation/choregos-{api,orchestrator,web,tools,runner}`,
multi-arch and signed. The chart's `global.imageTag` follows the chart version.

## Dependencies: embedded or external

Each dependency has an `embedded` switch, in the spirit of the Bitnami charts. **The default
is external** for all four — a serious installation brings its own PostgreSQL operator,
Temporal cluster and gateway.

```yaml
global:
  database:
    embedded: false                 # true deploys a single-instance PostgreSQL
    host: choregos-pg-rw.choregos-data.svc
    port: 5432
    name: choregos
    user: choregos
    secretRef: choregos-db          # key `url`
    passwordSecret: ""              # or an operator secret holding only the password
  temporal:
    embedded: false
    address: temporal-frontend.choregos-temporal.svc:7233
    namespace: default
  gateway:
    embedded: false                 # true deploys LiteLLM
    url: http://litellm.choregos-gateway.svc:4000
    secretRef: choregos-gateway-secrets   # key `master-key`
  memory:
    embedded: false                 # true deploys Ecphoria
    url: http://ecphoria.choregos-memory.svc:8432
    tokenSecret: ""                 # key `token`, empty means no auth header
```

Turning all four on gives a self-contained bench on a single node:
`-f charts/choregos/values/local.yaml`.

## Secrets

The chart never writes production secrets. It expects them to exist:

| Secret | Keys | Used by |
| :-- | :-- | :-- |
| `global.apiSecretRef` (default `choregos-api-secrets`) | `session-secret`, `run-token-private-key`, `run-token-public-key`, `github-app-id`, `github-app-private-key`, `github-webhook-secret` | API **and** orchestrator |
| `global.oidc.secretRef` | `client-secret` | API |
| `global.database.secretRef` | `url` (or `passwordSecret` holding the password) | API, orchestrator |
| `global.gateway.secretRef` | `master-key` | orchestrator |
| `global.memory.tokenSecret` | `token` | orchestrator |

`run-token-private-key` is the one to get right. The **orchestrator mints** run tokens and
the **API verifies** them, so both deployments read the same key. Give it only to the API and
every agent call comes back `Signature verification failed` — a message that blames the token
and never the missing key. Generate it with:

```bash
openssl ecparam -name prime256v1 -genkey -noout    # ES256 private key
```

The public key is derived from the private one when it is absent.

`global.devSecrets: true` makes the chart generate all of the above itself. It exists for a
bench and says so; never use it in production.

## Running agent stages

Agent stages run as disposable sandboxes. Which kind is `runner.executor`:

| Executor | Where it runs | Notes |
| :-- | :-- | :-- |
| `tekton` | A `PipelineRun` per stage | The default when Tekton is present |
| `k8s_job` | A `Job` per stage | Fewest moving parts; supports queueing |
| `aca` | Azure Container Apps job | Verified against a real subscription |
| `local_docker` | A container on the host | Development only |

```yaml
choregos-orchestrator:
  runner:
    executor: k8s_job
    namespacePattern: "proj-{slug}-runners"
    # Concurrent runs per namespace. Beyond it, the Job is created SUSPENDED: no pod,
    # no image pull, no quota taken, and it is admitted in arrival order. 0 = no cap.
    maxActive: 0
    limits: { cpu: "2", memory: 4Gi }
    envFromSecrets: [agent-credentials]   # mounted by reference into each agent pod
```

`maxActive` is the knob that keeps a burst of tickets from becoming a burst of pods. It is a
**soft** cap: two runs can admit themselves in the same window and exceed it by one. See
[ADR 0013](adr/0013-densite-des-taches-d-agent.md) for why there is no warm pool.

Under a namespace `LimitRange`, set `runner.limits` **below** the per-container ceiling.
Above it, the pod is rejected and the Job waits without saying why.

## Playbooks and the tool catalogue

Both are brought by the deployment, as ConfigMaps, and both are optional.

```yaml
global:
  playbooks:
    configMap: choregos-playbooks      # one key per role: <role>.md
    mountPath: /etc/choregos/playbooks
  toolCatalog:
    configMap: choregos-tools          # key: catalogue.yaml
    mountPath: /etc/choregos/outils
    fileName: catalogue.yaml
    credentialsSecret: choregos-tool-keys   # provider keys, injected into the API
```

Playbooks let a deployment replace **any** role prompt, including `implement`, without
touching the platform — that is what makes the engine usable outside software.

The tool catalogue declares third-party capabilities the platform uses **on behalf of** an
agent, with the provider credential staying server-side. Two kinds of entry:

```yaml
outils:
  # A plain HTTP API.
  - name: verifier_adresse
    description: Normalises a French postal address.
    provider: api-adresse-data-gouv
    input_schema: { type: object, required: [adresse], properties: { adresse: { type: string } } }
    http:
      method: GET
      url: https://api-adresse.data.gouv.fr/search/
      query: { q: "{{ adresse }}", limit: "1" }
    price_eur: 0.0

  # A tool from an MCP server OUTSIDE your organisation.
  - name: rechercher_entreprise          # the name YOUR agents see
    description: Looks a company up at a third-party provider.
    provider: annuaire-externe
    groups: [rh]                          # only projects in this group may use it
    input_schema: { type: object, required: [siren], properties: { siren: { type: string } } }
    mcp:
      url: https://mcp.provider.example/mcp
      tool: company_lookup                # the name THEY use
      headers: { Authorization: "Bearer {{ credential }}" }
    credential_env: FOURNISSEUR_MCP_KEY   # the variable name, never the key
    price_eur: 0.05
```

### The rules that make an external MCP server safe to add

- **The run token never leaves the platform.** It authenticates the agent *to us*; handing it
  to a third party would hand over the ability to write into the platform — post a result,
  file a finding, widen a scope. The remote receives `credential_env`, a key that is only
  good for it. A test asserts the run token appears in neither the headers nor the body of
  the outgoing request.
- **You expose a subset, under your own names.** The catalogue names the remote tool
  (`mcp.tool`) separately from the name your agents see. Publish three tools of a server that
  offers sixty; the agent never sees the URL nor the remote name.
- **Each tool opens to groups.** `groups: [rh]` on the tool, `groups: [rh]` on the project.
  **Two locks that say different things**: the deployment says *who is entitled*, the project
  says *what it uses*. Without the first, the project's own list would be the only control —
  and the project team can edit it. A tool with no `groups` restricts nothing.
- **The list does not discover itself.** Choregos never asks the remote server what it
  offers. The catalogue is written and reviewed in a pull request, and does not grow because
  a provider shipped something new.

A project declares what it calls in its own configuration (`tools: [verifier_adresse]`, or
`["*"]` for everything it is entitled to) and which groups it belongs to (`groups: [rh]`).
Both default to empty, which means **no tools at all**. See [ADR 0014](adr/0014-un-catalogue-d-outils-tenu-par-la-plateforme.md)
and `demo/outils/catalogue.yaml` for a working example that needs no key at all.

## Multi-tenant installations

For a tenant that may not create cluster-scoped resources:

```yaml
global:
  rbac:
    clusterScoped: false    # no ClusterRole is rendered
    jobRunner: false        # the platform grants the Job-launching Role, not the tenant
  externalSecrets:
    enabled: false          # secrets come from the tenant's own vault integration
```

## Database migrations

Alembic runs as a Helm hook before the API starts:

```yaml
choregos-api:
  migrations:
    hook: pre-install,pre-upgrade    # post-install,post-upgrade when the database is EMBEDDED
    waitForDatabaseSeconds: 300      # an init container waits for it
```

When the database is embedded it does not exist before the release, so the hook must run
*after* install — that is the one value to remember. The API only creates the schema by
itself on SQLite, a development convenience; on PostgreSQL the migrations are the single
source, and a test compares them against the models on every run.

## A single-node bench in twenty minutes

The bench runs real agents on a one-node kind cluster, with every dependency embedded. It
needs Docker, kind, kubectl, helm, and a node with 4 CPUs and 8 GB to spare. The first run
spends most of its twenty minutes building images.

```bash
make demo-up        # kind cluster, namespace, in-cluster git, ConfigMaps, the chart
make demo-images    # builds local/choregos-*:demo and LOADS them into the kind node
kubectl -n choregos create secret generic platform-llm-key --from-literal=api-key=sk-ant-…
kubectl -n choregos rollout restart deploy/choregos-litellm
make demo-seed      # two projects, five tickets, one Temporal workflow per ticket
make demo-status    # ticket states and the cost ledger
```

Two things are easy to miss and cost an afternoon each:

- **The images are local.** `demo/values-demo.yaml` sets `imagePullPolicy: Never`; without
  `make demo-images` every pod sits in `ErrImageNeverPull`.
- **The gateway needs a provider key.** The embedded LiteLLM mints one capped key per run and
  meters the spend — that is the only configuration in which the cost per ticket is a
  measured number. Without a provider key the bench can run in *direct* mode (the agent
  brings its own credentials) and **nothing is metered or capped** beyond turns and minutes;
  `demo/README.md` shows how, and says so.

What to expect: the three code tickets reach `done` in about ten minutes, the two HR
tickets reach `a_valider` in about five, the ledger shows non-zero tokens and euros under
`kind=model` and at least one `kind=tool` row (the HR agent verifies an address through the
catalogue). A ticket still in its initial state after fifteen minutes is not waiting — it is
dead; `temporal workflow describe` says why. `demo/README.md` has the rest, including what
this bench does **not** prove.

## The first organisation, and the first token

A fresh database has no organisation and no member. The chart bootstraps both:

```yaml
global:
  bootstrap:
    org: acme
    orgName: ACME
    admins: "alice@acme.example,bob@acme.example"
```

At every start the API creates the organisation if it is missing and gives those people
`org_admin` on it; it never touches what already exists. They can then create other
organisations (`POST /orgs`, `choregos orgs create`) and invite members.

The CLI and CI authenticate with API tokens, which a person issues for themselves once
logged in (`POST /me/tokens`, `choregos tokens create`). Before anyone has logged in, the
first token comes from the server side:

```bash
kubectl -n choregos exec deploy/choregos-api -- choregos-admin tokens create --email alice@acme.example
choregos login --api-url https://api.choregos.example --token chg_… --org acme
```

A project with no repository takes its requests from Choregos itself:

```bash
choregos projects create staffing --name "Staffing"            # no --repo: tracker `internal`
choregos items create staffing --title "Data PM for 6 months" --size M
```

## Identity: what the OIDC provider must give us

Any OIDC issuer works: the API reads `<issuer>/.well-known/openid-configuration` and uses
PKCE (S256) with a signed, single-use `state`. Register a confidential client with the
redirect URI `<api_url>/api/v1/auth/callback` and the scopes `openid profile email groups`.

Roles come from **groups**, and a group names its organisation:

| Group in the IdP | Role in Choregos |
|---|---|
| `choregos:<org>:org-admins` | `org_admin` on `<org>` |
| `choregos:<org>:product-owners` | `project_owner` |
| `choregos:<org>:release-captains` | `release_captain` |
| `choregos:<org>:developers` | `developer` |
| `choregos:<org>:viewers` | `viewer` |

Unprefixed groups (`developers`, `org-admins`) apply only when `global.oidc.defaultOrg`
names the organisation they belong to; otherwise they are ignored. Before 2026-09-24 a
group granted its role on **every** organisation of the instance.

**Row-level security is fail-closed.** Every request declares the organisations it may
see (from the caller's memberships, or `*` for the platform's own processes), and a session
that declares nothing sees nothing. Two consequences for the operator: the API **must not
connect as a PostgreSQL superuser** — a superuser ignores row-level security, so the API
refuses to start that way in `staging` and `prod` — and the embedded PostgreSQL therefore
creates a non-superuser role (`global.database.appUser`, `choregos_app`) at initdb. For a
volume created before 2026-09-24, create that role by hand and transfer ownership of the
schema to it (`docs/development.md` shows the statements).

**Development login** (`/auth/login?as=<email>`, no IdP) is off by default, refused by the
API itself in `staging` and `prod`, and turned on only by `values/local.yaml`
(`global.devLogin.enabled`, `global.devLogin.adminEmails`).

**API tokens** for the CLI and CI are issued by the person who will use them:
`POST /me/tokens` (or *Admin → Tokens*), shown once, expiring after 90 days by default.

## Verifying an installation

```bash
kubectl -n choregos get pods                      # API, web, four worker queues
kubectl -n choregos logs deploy/choregos-api      # structured JSON logs
curl -s https://<api-host>/healthz                # {"status":"ok"}
```

A ServiceMonitor, alerting rules and six Grafana dashboards ship with the chart. The API
exposes `/metrics` (Prometheus text format, computed from the database), and a test keeps
the dashboards and rules honest: they may only name metrics the API actually exports.

## Operating it

What the chart does for a multi-node installation, and the knobs behind it:

| Concern | Mechanism | Value |
| :-- | :-- | :-- |
| A node drain must not take the API, the front or a worker queue down | one `PodDisruptionBudget` per component, and per orchestrator queue — rendered only when there is more than one replica (a budget on a single replica blocks the drain and protects nothing) | `choregos-api.podDisruptionBudget`, `choregos-web.podDisruptionBudget`, `choregos-orchestrator.podDisruptionBudget` |
| Two replicas must not share a node | pod anti-affinity on `kubernetes.io/hostname`: `soft` (preferred, the default — a one-node bench still schedules), `hard` (one replica per node, or no pod), `none` | `global.antiAffinity` |
| The database must not be exhausted under load | a **bounded** connection pool per process: at most `size + maxOverflow` connections, a short wait beyond. Size PostgreSQL's `max_connections` as (API replicas + workers) × (size + maxOverflow), plus room for migrations and an operator | `global.database.pool.{size,maxOverflow,timeoutSeconds}` |
| A serious environment must not run on bench dependencies | the chart **refuses to render** in `staging` and `prod` when any `embedded` dependency, `devSecrets` or `devLogin` is on, and names the value | `templates/garde.yaml` |
| A worker must not stay attached to a dead node | tolerations of 20 s instead of Kubernetes' 300 s | `choregos-orchestrator.unreachableTolerationSeconds` |
| An agent must not reach the Internet except where the policy says | the runners namespace denies egress by default; the provisioning deploys a **Squid proxy per project** with the policy's `allow_domains`, and every agent pod gets `HTTPS_PROXY` pointing to it. No allowed domain, no proxy, no door | `policy.sandbox.network.allow_domains`, `choregos-orchestrator.runner.egressProxy` (empty on a bench), `runner.egressImage` to pin by digest |
| Temporal must be restorable | it holds the position of every ticket in flight; `docs/runbooks/temporal-backup.md` says what to copy and how to reconcile afterwards | — |

The proxy is the honest part of "egress allowlist": before 2026-09-25 the NetworkPolicy
opened a `choregos-egress` namespace that no chart delivered, and the allowlist was an
annotation. It is verified on a cluster (`tests/cluster/test_egress_proxy.py`: an
allowed domain answers 200 through the proxy, any other gets 403 from Squid).
