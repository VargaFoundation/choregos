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
  --version 0.4.1 --namespace choregos --create-namespace \
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

**Say what this installation can run.** A project created without an `agent` or `models`
section inherits the deployment's backend and model profiles; without them, it falls back to the
contract's defaults (`claude-code`, no profile), and a template's agents never reach a model on an
installation that serves another backend:

```yaml
global:
  agents:
    defaultBackend: opencode
    allowedBackends: [opencode]
    modelProfiles: { standard: platform/standard }   # profile → gateway model
```

A project that names its own `agent` or `models` keeps them. When a runner pod dies before it can
report — an image it cannot pull, a backend it cannot reach — the run says so: the runner's exit
code and what it means (`30`: agent backend unreachable, `50`: a skill missing or with a wrong
digest…), or why the container never started.

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

### How a tool reaches the agent

An agent never calls a provider. It calls an MCP server that runs **in its own pod**, on
`localhost:7777`, and that server calls the platform's internal API with the run's token. The
platform makes the outbound call, with the provider credential, and writes a `kind=tool` line to
the cost ledger.

That local address is always announced, and somebody always serves it:

| Executor | Who serves `localhost:7777` |
|---|---|
| `tekton` | a `sidecar:` in the Task |
| everything else | **the runner itself** (`runner/outils_locaux.py`), which starts the server when the announced address is local and nothing answers |

A sidecar, where one exists, keeps the port: the runner probes first and stands aside. A **non**-local
address is served by nobody automatically — that is a deployment providing its own server, and the
runner deliberately abstains.

Two things have to be true for an agent to actually get a tool, and both are easy to miss:

1. the deployment declares it in the catalogue (`global.toolCatalog.configMap`);
2. **the project declares that it uses it** (`tools:` in its configuration). The catalogue says
   what exists; the project says what it draws on. A tool that is in the catalogue and not in the
   project's list comes back `allowed: false`, and the agent correctly reports that it has no such
   tool — which is exactly what happened on the dev tenant on 2026-09-27, and cost an hour.

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

Alembic runs as a Helm hook before the API starts, through `python -m choregos_api.migrer`
(`upgrade heads`: the core, then the branch of any installed plugin that ships a schema):

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

**Hardened runtime for agent pods.** `choregos-orchestrator.runner.runtimeClass` (gVisor, Kata) applies to every agent pod unless the project policy demands gVisor, which always wins. The strict Kyverno rule in `infra/policies/pod-security.yaml` refuses any pod of a platform or project namespace without a non-root, seccomp-profiled security context — the chart's own pods are checked against it by `tests/charts`.

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

A scrape object, alerting rules and six Grafana dashboards ship with the chart. The API
exposes `/metrics` (Prometheus text format, computed from the database), and a test keeps
the dashboards and rules honest: they may only name metrics the API actually exports.

**Pick the flavour your platform actually runs.** `monitoring.flavour` is `prometheus` by
default and renders `ServiceMonitor` and `PrometheusRule` (`monitoring.coreos.com/v1`). Set it
to `victoriametrics` to render `VMServiceScrape` and a single `VMRule`
(`operator.victoriametrics.com/v1beta1`) instead:

```yaml
monitoring:
  flavour: victoriametrics
```

This is not cosmetic. An operator does not see a kind it does not know, so on a
VictoriaMetrics platform a `ServiceMonitor` is an **inert object**: nothing is scraped, no
alert is ever evaluated, and the namespace looks monitored. That is exactly what happened to
a real tenant on 2026-09-26. The alert rules themselves are written once and served to both
flavours, and a test fails if they ever diverge. An unknown flavour stops the render and names
itself.

### Bringing your own workflow templates

```yaml
global:
  workflowTemplates:
    configMap: mes-templates      # keys are <name>.yaml
    mountPath: /etc/choregos/workflow-templates
```

Mounted into **both** the API and the orchestrator, and `CHOREGOS_WORKFLOW_TEMPLATES_DIR` points
at it. Both, deliberately: the orchestrator runs the steps, but the API loads a workflow
definition and serves the template catalogue. Mounting it on one of them would give two processes
that do not know the same templates, and the disagreement would surface when someone creates a
project — far from the cause.

A deployment template wins over a shipped one of the same name. See
[Shipping a workflow template from outside this tree](development.md).

### A private registry

```yaml
global:
  imagePullSecrets: [harbor-prive]
```

Every pod the chart renders carries it — the four Choregos components **and** the embedded
dependencies, because a platform that admits a single registry sends every image through it, not
only ours. The secret must already exist in the namespace; on a shared platform it is usually the
tenant's base layer that creates it from a secret store, not this chart.

Without the value, no pod declares one. A test renders the chart both ways and fails if a new
pod is added without it.

A component can come from **another registry** than `global.imageRegistry` — the enterprise
edition serves its API and orchestrator images from a private project, the rest comes from the
core:

```yaml
choregos-api:
  image: { registry: harbor.example.org/choregos-ee, repository: choregos-api-ee }
choregos-orchestrator:
  image: { registry: harbor.example.org/choregos-ee, repository: choregos-orchestrator-ee }
```

The migration job follows the API's image. An image from another registry is another image:
`global.imageDigest` does not apply to it — pin it with its own `image.digest`.
`tests/charts/test_image_par_composant.py` renders both.

### Demonstration fakes (`demoFakes`)

`demoFakes.enabled: true` deploys **one** pod — the API image, `python -m
choregos_adapters.fakes.serveur` — serving the fakes of the HR scenario: MCP endpoints for the device
manager, the carrier, the badge readers and the supplier's agent
(`http://choregos-demo-fakes:8090/{mdm,shipping,access_control,fournisseur}/mcp`), and a fake
Microsoft Graph (`…/graph/v1.0`, `…/login`). One replica is the point: the API runs two and the
orchestrator is apart, and in-memory fakes would show each process its own directory. The network
policy opens that pod to the platform; `demoFakes.tokenSecret` (a secret reference) protects it
further. A demonstration: the chart refuses it in `staging` and `prod`. `essai/rh/` plays the HR
scenario against it.

### Which edition am I running?

```bash
curl -s https://<api-host>/edition
{"edition":"community","features":[],"version":"0.4.1"}
```

This repository is the **community edition**: Apache-2.0, and **one organisation**. Creating a
second one is refused with a 409 that names the decision behind it
([ADR 0024](adr/0024-deux-editions.md)). That is not an arbitrary cap — multi-tenancy is not
finished here, thirteen tables are still outside row-level security, and a single-organisation
install is exposed to none of it because there is nothing to cross.

The **enterprise edition** unlocks multiple organisations *and finishes the isolation*. It is
published separately, and it declares itself at start-up; the core never assumes it is there,
which is why the default is the most restrictive one.

The rights check runs **before** the edition check on purpose: a developer gets `403` and
learns nothing about the edition or the number of organisations. A product limit is not
explained to someone who has no right to meet it.

#### Announcing an edition, and why the chart refuses a half-announcement

`global.edition` (default `community`) is what the *deployment* claims. It reaches every process
as `CHOREGOS_EDITION`, and `/metrics` carries it as `choregos_edition_info{edition,version}` — so
a dashboard from three weeks ago still says which edition produced its numbers, which `/edition`
cannot do.

The claim and the reality are two different things, and they used to be unrelated. The edition
that *runs* is declared at start-up by the enterprise plugin; the chart value is a declaration by
a human. Set `global.edition: enterprise` on community images and you used to get a platform that
starts, looks healthy, serves metrics — and refuses the second organisation weeks later, a symptom
with no visible connection to its cause. Two guards now close that gap:

| Where | What it refuses | Why there |
|---|---|---|
| `helm template` | `edition: enterprise` with no `global.imagePullSecrets` | the enterprise images live in a private registry; that pull secret **is** the access control in phase 1 ([ADR 0024](adr/0024-deux-editions.md), decision 5). Without it the pods sit in `ImagePullBackOff`, which never names the cause |
| API start-up | `CHOREGOS_EDITION=enterprise` while no plugin declared itself | the image is the community one, or `choregos-ee` is not installed, or its `choregos.plugins` entry point does not call `declarer`. The refusal names all three |

The reverse is **not** refused: a deployment that announces nothing and loads the enterprise
plugin is simply one that forgot to say so, and denying it start-up would help no one. `/edition`
and the metric tell the truth either way.

### Row-level security covers identity (0.11)

Since 0.11, organisations, memberships, users and API tokens are under row-level security like the
rest: a session scoped to one organisation reads neither the name nor the members of another. Nothing
to configure — but the API must connect with a **non-superuser** role (a superuser ignores RLS), which
the chart already assumes.

### Webhook secrets

`CHOREGOS_GITHUB_WEBHOOK_SECRET` signs GitHub deliveries (HMAC). `CHOREGOS_GENERIC_WEBHOOK_SECRET` is
the shared secret of every other webhook — Argo CD, Alertmanager, Jira, GitLab, and **Tekton since
0.10.1**: Tekton's CloudEvents sink is configured by URL and cannot add a header, so the secret goes in
the sink URL, `…/api/v1/webhooks/tekton?jeton=<secret>`. Outside development, a webhook whose secret
is not configured is refused.

### When a secret is not a secret

The API **refuses to start** if any setting holds the literal string `<no value>` (or `<nil>`).
That is what a Go template — Helm, or the Infisical operator — writes when the variable it
references does not exist: it renders the words instead of failing. Ten characters that look
like a value.

This is not hypothetical. On 2026-09-26 a tenant ran with `GATEWAY_MASTER_KEY` and
`PLATFORM_LLM_KEY` both set to `<no value>`: every pod was `1/1 Running`, the namespace looked
healthy, and the upstream gateway answered `401 LiteLLM Virtual Key expected. Received=<no
value>` on the first model call. Nothing said the key had never resolved.

If the API refuses to start with `réglages non résolus`, fix the source of the secret — the key
is missing where it is stored, not where it is read.

## Operating it

What the chart does for a multi-node installation, and the knobs behind it:

| Concern | Mechanism | Value |
| :-- | :-- | :-- |
| A node drain must not take the API, the front or a worker queue down | one `PodDisruptionBudget` per component, and per orchestrator queue — rendered only when there is more than one replica (a budget on a single replica blocks the drain and protects nothing) | `choregos-api.podDisruptionBudget`, `choregos-web.podDisruptionBudget`, `choregos-orchestrator.podDisruptionBudget` |
| Two replicas must not share a node | pod anti-affinity on `kubernetes.io/hostname`: `soft` (preferred, the default — a one-node bench still schedules), `hard` (one replica per node, or no pod), `none` | `global.antiAffinity` |
| The database must not be exhausted under load | a **bounded** connection pool per process: at most `size + maxOverflow` connections, a short wait beyond. Size PostgreSQL's `max_connections` as (API replicas + workers) × (size + maxOverflow), plus room for migrations and an operator | `global.database.pool.{size,maxOverflow,timeoutSeconds}` |
| A serious environment must not run on bench dependencies | the chart **refuses to render** in `staging` and `prod` when any `embedded` dependency, `devSecrets`, `devLogin` or `demoFakes` is on, and names the value | `templates/garde.yaml` |
| A worker must not stay attached to a dead node | tolerations of 20 s instead of Kubernetes' 300 s | `choregos-orchestrator.unreachableTolerationSeconds` |
| An agent must not reach the Internet except where the policy says | the runners namespace denies egress by default; the provisioning deploys a **Squid proxy per project** with the policy's `allow_domains`, and every agent pod gets `HTTPS_PROXY` pointing to it. No allowed domain, no proxy, no door | `policy.sandbox.network.allow_domains`, `choregos-orchestrator.runner.egressProxy` (empty on a bench), `runner.egressImage` to pin by digest |
| Temporal must be restorable | it holds the position of every ticket in flight; `docs/runbooks/temporal-backup.md` says what to copy and how to reconcile afterwards | — |

The proxy is the honest part of "egress allowlist": before 2026-09-25 the NetworkPolicy
opened a `choregos-egress` namespace that no chart delivered, and the allowlist was an
annotation. It is verified on a cluster (`tests/cluster/test_egress_proxy.py`: an
allowed domain answers 200 through the proxy, any other gets 403 from Squid).
