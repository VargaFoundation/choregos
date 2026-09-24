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
[ADR 0013](../adr/0013-densite-des-taches-d-agent.md) for why there is no warm pool.

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

The tool catalogue declares third-party APIs the platform calls **on behalf of** an agent,
with the provider key staying server-side. See [ADR 0014](../adr/0014-un-catalogue-d-outils-tenu-par-la-plateforme.md)
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

## A single-node bench in ten minutes

```bash
kind create cluster --config demo/kind.yaml
kubectl create ns choregos
kubectl -n choregos apply -f demo/manifests/git.yaml

helm upgrade --install choregos charts/choregos -n choregos \
  -f charts/choregos/values/local.yaml -f demo/values-demo.yaml
```

`demo/README.md` has the rest: seeding two projects (one software, one HR), giving agents
model access, and — just as important — what this bench does **not** prove.

## Verifying an installation

```bash
kubectl -n choregos get pods                      # API, web, four worker queues
kubectl -n choregos logs deploy/choregos-api      # structured JSON logs
curl -s https://<api-host>/healthz                # {"status":"ok"}
```

A ServiceMonitor, five alerting rules and six Grafana dashboards ship with the chart.
