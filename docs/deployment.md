# Deployment

Choregos ships as a Helm umbrella chart with four workloads — API, web, Temporal workers,
and an optional MCP tools sidecar — plus four dependencies it can either **embed** or
**consume**.

## Prerequisites

| | Minimum | Notes |
| :-- | :-- | :-- |
| Kubernetes | 1.24 | `spec.suspend` on Jobs (GA in 1.24) is used to queue runs |
| Kubernetes, for hosted agents | 1.29 | A hosted agent runs as a native sidecar; below 1.29 its versions are refused (`hosted_unsupported_cluster`, [hosted agents](hosted-agents.md)) |
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
  objectStore:                      # file artifacts: decks, spreadsheets, images (ADR 0049)
    url: s3://choregos              # bucket and optional prefix
    endpoint: ""                    # an S3-compatible server (RustFS, MinIO); empty means AWS
    region: us-east-1
    secretRef: ""                   # keys `access-key`, `secret-key`; read by the API only
```

Only the API reads and writes the object store: a runner hands its files to the internal API
under its run token and never holds the store's keys. Under `CHOREGOS_FAKES=1` (or with a
`memory://` URL) the store is in memory and lost on restart.

An empty `endpoint` means AWS, and without `secretRef` the calls are unsigned: nothing fails at install, and
the first run that writes a file loses its run. The project's readiness (`object-store` item, `docs/usage.md`)
probes the bucket when a workflow declares a file output and names the cause.

Turning all four on gives a self-contained bench on a single node:
`-f charts/choregos/values/local.yaml`.

## Secrets

The chart never writes production secrets. It expects them to exist:

| Secret | Keys | Used by |
| :-- | :-- | :-- |
| `global.apiSecretRef` (default `choregos-api-secrets`) | `session-secret`, `run-token-private-key`, `run-token-public-key`, `github-app-id`, `github-app-private-key`, `github-webhook-secret` | API **and** orchestrator |
| `global.apiSecretRef`, entries `github-app-client-id`, `github-app-client-secret` (optional) | the App's OAuth client: needed only to claim an installation when the instance has several organisations ([the GitHub App](#the-github-app)) | API |
| `global.apiSecretRef`, entry `secrets-keks` (optional) | `CHOREGOS_SECRETS_KEKS`, see below | API **and** orchestrator |
| `global.oidc.secretRef` | `client-secret` | API |
| `global.database.secretRef` | `url` (or `passwordSecret` holding the password) | API, orchestrator |
| `global.gateway.secretRef` | `master-key` | orchestrator |
| `global.gateway.type` | `litellm` (default), `openai-compatible`, `direct`: the gateway adapter, as `CHOREGOS_GATEWAY_TYPE` | API, orchestrator |
| `global.memory.tokenSecret` | `token` | orchestrator |
| `global.objectStore.secretRef` | `access-key`, `secret-key` | API |

`run-token-private-key` is the one to get right. The **orchestrator mints** run tokens and
the **API verifies** them, so both deployments read the same key. Give it only to the API and
every agent call comes back `Signature verification failed` — a message that blames the token
and never the missing key. Generate it with:

```bash
openssl ecparam -name prime256v1 -genkey -noout    # ES256 private key
```

The public key is derived from the private one when it is absent.

**The secrets key** (`secrets-keks`, [ADR 0058](adr/0058-connector-secrets-are-write-only-values.md)) seals
the connector secrets typed in the console. It is a list `id:base64`, the first entry sealing and the
others only opening: `k2:<base64 of 32 bytes>,k1:<...>`. Generate one:

```bash
python -c "import os,base64;print('k1:'+base64.b64encode(os.urandom(32)).decode())"
```

Both the API and the orchestrator read it (`global.secrets.keksSecretRef`, default `apiSecretRef`;
`global.secrets.keksKey`, default `secrets-keks`; the entry is optional in the pod spec). Without
it, `env:` references keep working, the API logs `secrets.key_missing` at start-up, and typing a
secret answers 409. Keep it in the vault: **losing it loses every sealed value**. To rotate, put the
new key first, redeploy, then run `choregos admin secrets rewrap` (see
[the runbook](runbooks/rotation-secrets.md#connector-secrets-key-secrets-keks)) before dropping the
old key. `global.secrets.keksInExternalSecret: true` lists the entry in the chart's `ExternalSecret`
(turn it on once the key exists in the store: a missing remote property fails the whole object).

`global.devSecrets: true` makes the chart generate all of the above itself, the secrets key
included (drawn once, then re-read). It exists for a
bench and says so; never use it in production.

**Connector secrets by `env:NAME`.** A secret a connector references (`secret_refs: {api_token:
env:CONNECTOR_JIRA_TOKEN}`) must be mapped into the API and worker pods (`global.extraEnv` with
`valueFrom: {secretKeyRef: …}`, from a Secret) under a name matching `global.secrets.envPattern` —
by default `^CONNECTOR_[A-Z0-9_]+$`, set as `CHOREGOS_SECRET_ENV_PATTERN`. Widen the pattern for your own naming; `CHOREGOS_*`,
`DATABASE_*`, `PG*`, `AWS_*`, `KUBERNETES_*` and `TEMPORAL_*` stay refused whatever it says. After
an upgrade, a stored reference outside the pattern stops resolving (the connector shows in error):
rename its variable or widen the pattern.

## The GitHub App

One App serves the deployment (`github-app-id`, `github-app-private-key`). Its settings on github.com
decide whether other companies can use it and whether a console install finds its way back:

| Setting | Value |
| :-- | :-- |
| Permissions | Repository: Contents, Issues, Pull requests, Checks: read and write; Metadata: read |
| Events | Issues, Issue comment, Pull request, Pull request review, Check suite, **Installation**, **Installation repositories** |
| Setup URL | `<public url>/api/v1/github/setup`, with **Redirect on update** ticked. A browser redirect: it works over a VPN |
| Webhook URL | `<public url>/api/v1/webhooks/github` (needs a host GitHub can reach; discovery does not depend on it) |
| Where can this GitHub App be installed? | **Any account** if companies other than the App's owner use the deployment (a private App installs only on its owner's account) |
| Request user authorization (OAuth) during installation | **Tick it before a second organisation exists**, generate a client secret, and give `github-app-client-id` / `github-app-client-secret` to the API |

The Setup URL is a browser page. A browser that comes back without a console session is sent to sign
in and then back to it, and an installation started on github.com rather than from the console lands on
the console without being claimed: on an instance with one organisation the next sync claims it, with
several the organisation installs it again from the console.

Nobody types an installation id ([ADR 0073](adr/0073-github-app-installations-are-discovered-not-typed.md)):
the console lists what the App sees, *Install on GitHub* sends the person to github.com and back through
the Setup URL, and the installation is **claimed** by the organisation that started the flow. On an
instance with one organisation every installation is claimed implicitly; with several, a claim is
refused (403, naming `CHOREGOS_GITHUB_APP_CLIENT_SECRET`) until the OAuth client is configured.

## Running agent stages

Agent stages run as disposable sandboxes. Which kind is `global.runner.executor`
([ADR 0072](adr/0072-agent-sandbox-is-the-runtime-of-a-real-deployment.md)): the API and the
workers read the same setting, and `agent_sandbox` is the default.

| Executor | Where it runs | Notes |
| :-- | :-- | :-- |
| `tekton` | A `PipelineRun` per stage | Only where written: `global.runner.executor: tekton` |
| `k8s_job` | A `Job` per stage | Fewest moving parts; supports queueing |
| `aca` | Azure Container Apps job | Verified against a real subscription |
| `local_docker` | A container on the host | Development only |
| `agent_sandbox` | A claimed sandbox from a warm pool (`kubernetes-sigs/agent-sandbox`) | **The default**; needs the operator (the chart fails at render without it); supports queueing |

```yaml
global:
  runner:
    executor: k8s_job
    # What a project may pick as its `runtime` connector. Empty: only `executor`.
    allowedExecutors: []
choregos-orchestrator:
  runner:
    # Empty follows the executor: the release namespace for agent_sandbox,
    # `proj-{slug}-runners` for the others.
    namespacePattern: "proj-{slug}-runners"
    # Concurrent runs per namespace. Beyond it, the Job is created SUSPENDED: no pod,
    # no image pull, no quota taken, and it is admitted in arrival order. 0 = no cap.
    maxActive: 0
    limits: { cpu: "2", memory: 4Gi }
    envFromSecrets: [agent-credentials]   # mounted by reference into each agent pod
```

`maxActive` is the knob that keeps a burst of tickets from becoming a burst of pods. It is a
**soft** cap: two runs can admit themselves in the same window and exceed it by one. See
[ADR 0013](adr/0013-agent-task-density.md) for the density reasoning (its "no warm pool" is
superseded by [ADR 0072](adr/0072-agent-sandbox-is-the-runtime-of-a-real-deployment.md)).

**What a project may pick.** The API knows the executor (`CHOREGOS_EXECUTOR_KIND`) and the
allowed list (`CHOREGOS_EXECUTORS_ALLOWED`, from `global.runner.allowedExecutors`). `GET
/platform/executors` answers from them, and `PUT /projects/{id}/connectors/runtime` refuses (422)
a type outside the list and names the allowed ones.

**Upgrading from 0.17 or earlier**, which defaulted to Tekton: set
`global.runner.executor: tekton` to keep it. An override of only
`choregos-orchestrator.runner.executor` fails the render and names the global setting.

Under a namespace `LimitRange`, set `runner.limits` **below** the per-container ceiling.
Above it, the pod is rejected and the Job waits without saying why.

### Images for jobs and for agents written in code

A `job` step ([ADR 0056](adr/0056-a-deterministic-step.md)) and an agent written in code
([ADR 0063](adr/0063-agents-written-in-code.md)) may run in their own image. Neither names a
reference: they name an image, and the operator maps names to references.

```yaml
global:
  images:
    jobs:   { quant: harbor.example.com/choregos/quant@sha256:… }
    agents: { ticket-triager: harbor.example.com/agents/ticket-triager@sha256:… }
    requireDigest: true     # the default: a tag-only reference stops the API and the workers at start-up
```

The chart renders them as `CHOREGOS_JOB_IMAGES`, `CHOREGOS_AGENT_IMAGES` and
`CHOREGOS_REQUIRE_IMAGE_DIGEST` on the API (which refuses a version naming an unknown image and lists
the names at `GET /platform/agent-images`, never the references) and on every orchestrator worker
(which resolves the name when the stage starts, and fails the step without a retry on an unknown one).
Each image is built **from** the runner image: the runner is the entrypoint, the agent is a
subprocess. `requireDigest: false` is for a bench only. The `agent_sandbox` executor claims from one
warm pool built from the runner image: use `k8s_job` for steps that name their own image.

### Registries for hosted agents

A hosted agent ([ADR 0080](adr/0080-hosted-prompt-and-voice-agents.md),
[hosted agents](hosted-agents.md)) names its own image by reference. The operator says where such a
reference may come from; the version stores the digest the registry served at publication.

```yaml
global:
  images:
    hostedRegistries: [ghcr.io/acme, registry.internal]   # the default, [], accepts no image source
    hostedCosign: "off"                                     # off | warn | enforce
    hostedCosignKeySecret: ""      # a Secret holding `cosign.pub`, or keyless:
    hostedCosignIdentity: ""       #   the certificate identity, a regular expression
    hostedCosignIssuer: ""         #   its OIDC issuer, a regular expression
    hostedCosignTlog: true         # false: skip Rekor, for a key whose signatures were never uploaded
    hostedCosignPolicy: false      # render the Kyverno ClusterPolicy `choregos-hosted-images`
```

Hosted agents need Kubernetes 1.29 or later. The chart renders the cluster's version as
`CHOREGOS_CLUSTER_VERSION` on the API, from `global.kubernetesVersion` or, empty, from the version Helm
renders for: the cluster's own on `helm install` and under Argo CD, Helm's built-in default under an
offline `helm template` (pass `--kube-version` or set `global.kubernetesVersion` there). The executor
also reads the API server's `/version` before it starts a hosted step.

The chart renders `CHOREGOS_HOSTED_REGISTRIES` and `CHOREGOS_HOSTED_COSIGN` on the API only. A prefix
matches by path segment. The API reads the manifest through the organisation's egress allowlist, so the
organisation must open the registry's host (and its token host, e.g. `ghcr.io` hands tokens from
`ghcr.io/token`). Pulls are anonymous: a private registry is not supported yet. The signature is cosign's,
by the key or the keyless identity above; `enforce` with neither refuses every image. The signer's
variables (`CHOREGOS_HOSTED_COSIGN_KEY`, `_IDENTITY`, `_ISSUER`, `_TLOG`) and the key's mount are on the
API only; [`hosted-agents.md`](hosted-agents.md#signatures-by-cosign-s41-14) says what is verified.

### The `agent_sandbox` executor (the default)

[ADR 0016](adr/0016-agent-sandbox-executor.md) adds an executor on
[`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox): a warm pool
of sandboxes that have never run anything, and a sandbox that can be suspended across a human
question. It needs the operator, whose CRDs are **cluster-scoped**: a platform team installs it,
a tenant of a shared cluster cannot. It is the default executor
([ADR 0072](adr/0072-agent-sandbox-is-the-runtime-of-a-real-deployment.md)): the chart checks at
render that `extensions.agents.x-k8s.io/v1beta1/SandboxTemplate` is among the cluster's API
versions (Argo CD and `helm install` pass them) and fails, naming the operator and
`global.runner.executor: k8s_job`, when it is not. An offline rendering passes
`--api-versions extensions.agents.x-k8s.io/v1beta1/SandboxTemplate` or sets
`runner.skipOperatorCheck: true`. The namespace's volume quota must cover
`sandbox.storage × (maxActive + warmPool.size + runs finished within the volume's lifetime)`.

1. **The operator**, pinned to v1.0.6 (API `agents.x-k8s.io/v1beta1` and
   `extensions.agents.x-k8s.io/v1beta1`): `infra/bootstrap/agent-sandbox.yaml` is the Argo CD
   Application, `infra/operators/agent-sandbox/` the pinned source (upstream tag and controller
   image digest). Without Argo CD, the same thing by hand, once:
   `kubectl apply --server-side -k infra/operators/agent-sandbox`.
2. **The policy**: `infra/policies/sandbox-template.yaml` applies Kyverno's non-root rule to the
   pod a SandboxTemplate or a Sandbox describes, so a template that breaks it is refused where it
   is written, not pod after pod in the controller's log.
3. **The chart**:

```yaml
global:
  runner:
    executor: agent_sandbox           # the default, written here to make the choice visible
    runtimeClass: gvisor              # the template's RuntimeClass; "" = the cluster's
choregos-orchestrator:
  runner:
    # namespacePattern empty = the release namespace: the template lives there
    warmPool: { size: 2 }             # sandboxes made in advance, never used, holding nothing
    sandbox: { storage: 10Gi, storageClassName: "", assignmentPort: 8765, ttlSecondsAfterFinished: 300 }
global:
  rbac: { jobRunner: true }           # or the platform grants the same verbs (see rbac-jobs.yaml)
```

A finished run's sandbox keeps its workspace volume for `ttlSecondsAfterFinished` seconds, then
the controller deletes it. Until then the volume counts against the namespace's quota: size
`storage` × (runs finished in that window + warm pool) against it. At 3600, one ticket's six
stages filled the dev tenant's 10 PVCs / 50Gi, and the next claim found no volume.

The warm pool is rendered with `updateStrategy: Recreate`: when the template changes (an
upgrade changes the runner image), the controller deletes the unclaimed warm sandboxes and makes
new ones. With the operator's default, `OnReplenish`, a stale sandbox stays in the pool until a
claim takes it, so the first run after an upgrade ran in the previous version's runner.

The chart also renders `choregos-runner-assignment`, a NetworkPolicy that lets only the
orchestrator's `executor` workers reach the assignment port. Where the platform lays the
namespace's NetworkPolicies and refuses a tenant's (an Argo CD AppProject that blacklists
`NetworkPolicy` fails the whole sync on that one object), set
`runner.sandbox.assignmentNetworkPolicy: false`: the platform must then close that port to every
other pod of the namespace itself. Otherwise any pod there can push first and hold a warm sandbox
until the real run gets a 409: it cannot read a run's token (the assignment is sealed to the
pod's key), but it can deny the run its sandbox.

How a run goes ([ADR 0053](adr/0053-agent-sandbox-as-built.md)): the executor writes the run's
token into a Secret owned by the run's `SandboxClaim`; the controller binds a warm sandbox to
the claim (or cold-starts one from the template when the pool is empty); once it is Ready, the
executor reads the public key the pod printed at boot (through the API server, `pods/log`), seals
the token and the run's environment to it, and pushes only that ciphertext; the pod opens it,
drops its key, and serves that one run for its whole life. Beyond `maxActive`, a run waits as its Secret alone, with no claim and no pod, and
is admitted in arrival order. A policy that asks for a RuntimeClass the pool was not made with
is refused, never run without it. The same goes for the image: a pool's pods are made from one
image (`CHOREGOS_RUNNER_IMAGE`, or `pool_image` in the connector), so a step that names its own
image (a `job` with `image:`) is refused with a `ConfigurationError` instead of running in the
pool's image. A deployment on `agent_sandbox` sends such a step to a second executor,
`runner.customImageExecutor` (default `k8s_job`; empty keeps the refusal): the choice is made at
start on the step's image, and the run's executor kind, persisted per run, routes its status, logs
and cancel afterwards. Add `k8s_job` to `global.runner.allowedExecutors`. A run on the second
executor has that executor's capabilities (no suspension across a question).

When an agent asks a question, its sandbox is **suspended** (pod gone, workspace volume kept)
and the answer resumes the same run in it: one run, not two. The policy bounds the wait:
`sandbox.suspend_max_hours` (default 24, `0` = never suspend); past it the sandbox is deleted
and the answer replays the stage. The workspace survives, and so does the agent's
conversation when the agent can reload it (ACP `loadSession`); otherwise the run journal says so
(`session/load.unsupported`) and the agent starts a new conversation in that workspace, with the
answer at the head of its prompt.

The chart then renders a `SandboxTemplate` and a `SandboxWarmPool`, both named
`choregos-runner`. A warm sandbox's pod mounts no service account token and no secret: its
runner waits for one assignment, pushed by the executor after a claim has bound the sandbox to a
run. A per-project `namespacePattern` (`proj-{slug}-runners`) is refused at render time; the
provisioning does not write a template per project yet.

To prove it on a cluster that has the operator (non-destructive: one namespace of its own,
deleted afterwards), name that cluster explicitly:

```bash
CHOREGOS_CLUSTER_CONTEXT=<context> CHOREGOS_CLUSTER_ALLOW_SHARED=<context> make test-cluster
```

Each capability the executor announces (`queue`, `renew`, `snapshot`, `suspend`, `resume`) has a
test there marked `@pytest.mark.capability("agent_sandbox", …)`; CI refuses an executor that
announces a capability without one.

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

These values, and `global.runner.allowedExecutors` / `CHOREGOS_EXECUTOR_KIND` for the executors,
are where backends and executors are configured; *Administration → Platform* only displays them
(its backend switch is enforced at stage start and feeds readiness; there is no `PUT /platform/executors`, S42-07b).

A project that names its own `agent` or `models` keeps them. When a runner pod dies before it can
report — an image it cannot pull, a backend it cannot reach — the run says so: the runner's exit
code and what it means (`30`: agent backend unreachable, `50`: a skill missing or with a wrong
digest, `60`: an input that does not match its digest, `80`: the context reduction the policy requires did not start, `90`: confinement required and the kernel cannot confine the agent, ADR 0094…), or why the container never started.

## Theme

The community edition serves one theme, its default (`GET /api/v1/theme`, public). Customising the
look (colours per mode, radii, title font, logo, product name, footer, default mode) is an
enterprise feature: a plugin calls `choregos_api.greffons.declare_theme_provider(fn)` with
`fn(session, org) -> dict | None` and the core validates what it returns (schema, then the
console's contrast budget on the effective colours). An invalid theme is replaced by the default
(`X-Choregos-Theme: fallback`, with the violations in the API log); a plugin can check a theme
itself with `choregos_api.theme.validate_theme`. A theme cannot change the mono font, the layout or
the attribution line. Fonts and images are served by the API under `/api/v1/` so that the console's
CSP stays unchanged; hand files to a plugin with `choregos-api.extraVolumes` (S39-03c).
The console reads the instance theme on the server before drawing anything (`CHOREGOS_API_URL`,
1.5 s timeout, cached 60 s; any failure gives the default look) and applies it as CSS variables in
the page, so the first paint is already themed; the person's organisation theme replaces it right
after hydration. A person's own light/dark choice always wins over the theme's default mode. See
[ADR 0064](adr/0064-a-theme-is-data-the-console-applies.md).

An organisation's own look (S42-13) follows the person, not only the instance:
- When the person picks or is given an organisation, the console remembers its slug in
  `localStorage` (`choregos.org`) and in the non-HttpOnly cookie `choregos_org`. The root layout
  reads the cookie and asks `GET /api/v1/theme?org=<slug>`, so the first paint, sign-in page
  included, already wears that organisation's name, logo, favicon, colours and footer. The cookie
  only SELECTS a public look: it grants nothing, a value that is not a slug (`[a-z0-9-]`, 63
  characters at most) is ignored, and the theme is memoised per organisation for 60 s in a
  bounded cache (32 entries).
- The footer sentence, the attribution line and the favicon follow the theme the client holds, so
  they change with the organisation once it is swapped in. The attribution to the foundation stays.
- A long product name is cut in the header and shows whole in a tooltip.
- A logo is a file the organisation uploads, never a URL (the console's CSP serves images from
  `'self'`, `data:` and `blob:` only). A theme editor in an edition offers it as a form field of
  `format: "asset"` (ADR 0032 addendum); the community core serves no organisation theme, so
  it has no such upload route.

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
    result_max_bytes: 16384               # optional: how much of an answer the agent reads
```

`result_max_bytes` (at least 1024) replaces `CHOREGOS_BROKER_RESULT_MAX_BYTES` (64 KiB) for that
entry; `CHOREGOS_BROKER_RESULT_CEILING_BYTES` (1 MiB) bounds both, and an organisation's
per-operation caps on its connectors too ([usage](usage.md), S40-17).

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
Both default to empty, which means **no tools at all**. See [ADR 0014](adr/0014-a-platform-held-tool-catalogue.md)
and `demo/outils/catalogue.yaml` for a working example that needs no key at all.

## Network access: the egress allowlist and its ceiling

The organisation's administrator decides, in the console, which hosts the platform may call for
the organisation ([ADR 0066](adr/0066-the-organisation-egress-allowlist.md)), within the bounds of
its policy ceiling's `network` section ([ADR 0070](adr/0070-network-access-is-bounded-and-enforced-per-run.md),
amended: one model). What only the operator sets is one `global.egress` block — the keys below and
those of the per-run egress proxy (`enabled`, `denyDomains`…, further down) — rendered as `CHOREGOS_EGRESS_MODE`,
`CHOREGOS_EGRESS_CEILING` and `CHOREGOS_EGRESS_PLATFORM_CHANGE` on the API and every worker:

```yaml
global:
  egress:
    mode: enforce          # enforce | warn | off
    ceiling:               # mirror the cluster's network policy, per pod family
      api: [api.github.com, github.com]
      workers: [api.github.com, "*.atlassian.net"]
      runners: [github.com, pypi.org]
    platformChange:
      format: text         # cilium-fqdn | kubernetes-networkpolicy | text
      owner: "the platform team"
      entries: {api: api-github, workers: provisioning, runners: runner}   # opaque template data
      template: |
        platform/tenant-base/values-choregos-dev.yaml, entry `{{entry}}` (selector {{selector}}), add:
        {{#hosts}}  - {{.}}
        {{/hosts}}Reason: {{reason}} (requested by {{requested_by}}, origin {{origin}}).
      issueUrl: "https://github.com/ACME/platform/issues/new?title=egress%3A%20{{hosts_csv}}&body={{snippet_urlencoded}}"
```

- **`ceiling`** is what the cluster lets through. An absent or empty family is *unknown*: it is not
  checked and the organisation's list alone governs. Keep it in step with the cluster's policy:
  nothing verifies the mirror.
- **`template`** is mustache: `{{family}}`, `{{selector}}`, `{{entry}}`, `{{hosts_csv}}`,
  `{{ports}}`, `{{reason}}`, `{{origin}}`, `{{requested_by}}`, `{{#hosts}}…{{.}}…{{/hosts}}` and
  `{{#fqdns}}` (`matchName: "h"` lines). Without one, the console shows a neutral Cilium `toFQDNs`
  fragment per family. `issueUrl` takes the same placeholders, URL-encoded, plus
  `{{snippet_urlencoded}}`. Choregos can prefill an issue; it cannot open a pull request.
- **`mode`**: `enforce` refuses a host the organisation has not allowed (and a call with no policy
  in context); `warn` logs `egress.would_refuse` and passes; the code default under
  `CHOREGOS_FAKES=1` is `warn`.
- **Upgrading**: the migration seeds an `allowed` rule for every host an organisation already calls
  (connectors, web and git knowledge sources, skill sources). Review them in `/admin/egress`.

## Extra volumes for the API

`choregos-api.extraVolumes` and `choregos-api.extraVolumeMounts` are appended verbatim to the
API pod and container, so a deployment can hand files to a plugin without forking the chart:

```yaml
choregos-api:
  extraVolumes: [{ name: theme, configMap: { name: my-theme } }]
  extraVolumeMounts: [{ name: theme, mountPath: /etc/choregos/theme, readOnly: true }]
```

A volume named like one the chart owns (`tmp`, `catalogue`, `workflow-templates`, `playbooks`, `mcp-catalogue`)
fails at render.

### The skills catalogue: egress for git delivery

A `bundled` skill of the skills catalogue is in the image and needs no network. A `git` entry (a
copyleft licence, a high-risk skill) is fetched at its pinned commit from **`codeload.github.com`**, as
the existing *Import from a repository* is: the API pod's egress must allow that host (an egress rule
in your network policy). `choregos-api.egressHosts` is only the hint the console shows (`egress:
undeclared` on a `git` entry when the list is set and lacks `codeload.github.com`); it restricts
nothing. Without the egress rule, installing a bundled skill still works and a git one answers `502`.

### The MCP server catalogue: egress and your own entries

The image ships a catalogue of well-known MCP servers ([ADR 0065](adr/0065-catalogues-of-mcp-servers-and-skills.md));
an administrator installs an entry from *Catalogue → MCP servers* (it then appears under *Administration → Installed connectors*). Two chart values
belong to the deployment:

- `choregos-api.egressHosts` (a list, `*.` wildcards allowed) declares the hosts the API pod may
  reach. It becomes `CHOREGOS_API_EGRESS_HOSTS` and **restricts nothing**: your network policy
  does. The catalogue uses it to tell an administrator, before a silent timeout, that a server's host
  (`mcp.notion.com`) is `undeclared`. Keep it equal to the egress allowlist of the namespace, and add
  a host there (an egress rule, then here) before enabling a server that needs it. Empty: the
  catalogue says `unknown` and warns about nothing.
- `choregos-api.mcpCatalogueExtra` is the name of a ConfigMap holding a `catalogue.yaml`
  (`entries: [...]`, the fields of the shipped catalogue) that lists **your own** MCP servers, merged
  after the shipped ones and shown as `site` entries. An id already in the shipped catalogue is
  refused, not shadowed, and a malformed file makes the catalogue answer `422` naming the entry.

A server that authenticates per user with OAuth is listed but cannot be installed yet, and a server
that runs as a local process (stdio) is listed with what to run: neither is something an install
button can do.

## Site notes on the setup procedures

The console shows a setup procedure beside every "add" form (a connector, a knowledge source, an
external agent): what to create at the vendor, then where to paste what, with this platform's webhook
and callback addresses. They ship with the platform ([`procedures/`](procedures/README.md),
[ADR 0067](adr/0067-a-procedure-per-connector-and-agent-type.md)). A deployment adds what is true only
here: who registers an Entra app on this platform, where an egress change is requested, how secrets
are handled.

Put one Markdown file per procedure in a ConfigMap and name it in the API's values:

```yaml
choregos-api:
  proceduresExtra: choregos-procedures   # a ConfigMap; mounted read-only at /etc/choregos/procedures
```

- `<id>.site.md` appends an **On this platform** section to the procedure `<id>`
  (`connector.documents.sharepoint.site.md`). Plain Markdown, no front matter; a leading
  `## On this platform` heading is dropped. Placeholders such as `{{api_url}}` work. A link must be
  `https` and appear in the procedure's `vendor_docs`, otherwise the file is refused.
- `<new-id>.md` adds a whole procedure for a type the platform does not know (a plugin's), with the
  front matter of the shipped ones. It cannot replace a shipped id.

A refused file is logged (`procedures.file.refused`) and skipped: the API still starts and the other
files still apply. A plugin can supply the same directory through a `choregos.procedures` entry point
whose value is a path. Outside the chart, the setting is `CHOREGOS_PROCEDURES_EXTRA`.

## Plugins

A plugin packages agents, skills, templates, evals, procedures and MCP servers in one signed format
([ADR 0093](adr/0093-an-agent-ships-as-a-plugin.md), [writing one](plugins.md)). The deployment says
where plugins come from, which ones every organisation gets, and which ones are baked into the image:

```yaml
global:
  plugins:
    sources:
      - name: varga
        index: oci://ghcr.io/vargafoundation/choregos-agents/index:stable   # or https://…/index.yaml
        trust:                    # a keyless cosign signer (regular expressions, matched whole)…
          identity: https://github.com/VargaFoundation/choregos-agents/.*
          issuer: https://token.actions.githubusercontent.com
          # key: /etc/choregos/plugins-keys/acme.pub   # …or a public key, mounted with api.extraVolumes
        egress: []                # hosts its fetches may reach beyond the index's own
    defaultPlugins:
      - { source: varga, name: servicenow, version: "^0.1" }
    offline: /opt/choregos/plugins   # plugins baked into the image: air-gapped, demo, CI
```

The chart renders these values into the ConfigMap `choregos-plugins`, mounted read-only in the API at
`/etc/choregos/plugins/plugins.yaml` and named by `CHOREGOS_PLUGINS_CONFIG`. All three are empty by
default: nothing is rendered and no plugin is installed. A source without `trust` refuses to render;
there is no unsigned source.

**At start-up**, the API reads the file before it serves anything. A file it cannot read, an invalid
source, a default plugin naming an undeclared source, an `offline` directory that does not exist in the
image, or two different offline directories (`offline` here and `CHOREGOS_PLUGINS_OFFLINE` in the
image's environment; set one) stop the API with a message naming the setting, rather than a 500 at the
first install.

**Default plugins.** Once started, the API installs each `defaultPlugins` entry in every organisation,
in the background (a slow registry never holds the readiness probe), and in each organisation created
later, at its creation:

- it is idempotent: an organisation that has the plugin, at any version, is left alone; running it
  again changes nothing. Moving to a newer version stays an administrator's gesture (an upgrade);
- each replica runs it, one organisation at a time under a PostgreSQL advisory lock: two replicas
  starting together take turns, and the second finds everything done;
- every fetch passes the same checks as an administrator's install (index, signature, pinned commit,
  digest, `plugins.lock`, contents, core range). A plugin that fails is logged
  (`plugin.default.failed`) and the others go on;
- a default plugin that brings an MCP server is **not** installed by the platform: it waits for an
  administrator's approval, like any plugin with a server. It is logged
  (`plugin.default.pending_approval`) and listed by `GET /api/v1/orgs/{org}/plugin-approvals` with
  `default: true`; any administrator of the organisation approves it there.

What the reconcile installed is listed with `default: true` and `installed_by: null`. The summary of
each pass is the log line `plugin.default.reconciled` (installed, waiting, failed).

**Egress.** Fetches go through the API's egress guard, opened for each source's own hosts within the
deployment's ceiling (`global.egress`); the API's network policy must also let it reach the index and
registry hosts (`choregos-api.egressHosts` documents them for the catalogues).

### Plugin servers

A plugin's MCP server runs in the cluster, started by the API ([ADR 0096](adr/0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md),
[what a server must do](plugins.md#servers)). Off by default: a plugin that brings one is refused.

```yaml
global:
  plugins:
    servers:
      enabled: true
      namespace: { name: choregos-plugins, create: false }   # create: true renders the Namespace too
      registries: [ghcr.io/vargafoundation/choregos-agents]   # where a server's image may come from
      runtimeClass: ""          # empty: global.runner.runtimeClass, else the cluster's runtime
      egress: networkpolicy     # or cilium: a CiliumNetworkPolicy names the exact hosts
      resources: { limits: { cpu: 500m, memory: 512Mi } }
```

With `enabled`, the chart renders a Role in that namespace (Deployments, Services, NetworkPolicies,
Secrets; CiliumNetworkPolicies with `egress: cilium`) bound to the API's ServiceAccount, nothing
cluster-wide, and opens the platform's egress towards the namespace. The namespace must not be the
release's. Per organisation and server the API writes a Deployment, a Service, a NetworkPolicy (ingress
from the API's pods only) and a Secret, named `<server>-<hash of the organisation>` and labelled
`choregos.dev/plugin`, `choregos.dev/server`, `choregos.dev/org`.

What stays with the operator: the namespace (unless `create`), the registry allowlist in two places —
`registries` here and the prefix of the Kyverno policy `choregos-plugin-server-images`
(`infra/policies/plugin-servers.yaml`, the namespace name too if it is not `choregos-plugins`) — and,
for `cilium`, a cluster that runs Cilium with its DNS proxy. Signatures follow `images.hostedCosign`
and its signer.

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

**Hardened runtime for agent pods.** `global.runner.runtimeClass` (gVisor, Kata; `""` = the cluster's) applies to every agent pod unless the project policy demands gVisor, which always wins. It is the RuntimeClass of the `agent_sandbox` warm pool's template and reaches the API and every worker (`CHOREGOS_RUNNER_RUNTIME_CLASS`); it is deliberately not set by default. An install that sets only `choregos-orchestrator.runner.runtimeClass` renders as before, but the API is then told nothing and cannot judge a gVisor policy (no `runtime-class` item, no false alarm); both set must agree, else the render is refused. With `global.runner.runtimeClass` set, a project whose policy asks for gVisor on a pool that does not run it sees a `runtime-class` item in its readiness (`docs/usage.md`) instead of a run stuck in `queued`. The strict Kyverno rule in `infra/policies/pod-security.yaml` refuses any pod of a platform or project namespace without a non-root, seccomp-profiled security context — the chart's own pods are checked against it by `tests/charts`.

**Installing the admission policies.** `kubectl apply -k infra/policies` installs the five default ClusterPolicies; they need Kyverno ≥ 1.12 and the agent-sandbox operator's CRDs, nothing else (`tests/charts/test_policies_install_on_a_bare_cluster.py`). Where Tekton runs (`global.runner.executor: tekton`), add `kubectl apply -k infra/policies/tekton`: its `PipelineRun` rule is kept apart because Kyverno refuses a whole policy whose kinds the cluster does not know.

**Context reduction kill switch.** `choregos-orchestrator.runner.contextReduction.enabled: false` (env `CHOREGOS_CONTEXT_REDUCTION=off`) refuses rtk and headroom to every run, whatever a project's policy asks ([ADR 0055](adr/0055-fewer-tokens-per-run.md)). Left at `true`, each policy decides, and a policy without `context` reduces nothing.

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

`global.bootstrap.org` is the **slug** of the first organisation and `orgName` its display name:
name them for the installation (there is no default organisation, neither in the chart nor in the
CLI). The console shows the organisation a person last picked, else the first one `GET /orgs`
returns; `NEXT_PUBLIC_DEFAULT_ORG` is a build-time variable of the image and the chart does not set it.

The bootstrap **never renames** an organisation that exists: changing `bootstrap.org` on a running
instance creates a second organisation (refused on the community edition, which holds one). To
rename, call `PATCH /api/v1/orgs/<slug>` with `{"name": "…"}` (an organisation administrator), or
`{"slug": "…"}` (a platform administrator, who need not be a member). The old slug stays an
alias of the organisation (ADR 0090): the IdP groups (`choregos:<old>:<group>`), every MCP address
(`/mcp/projects/<old>:<project>`), every copied console URL, `bootstrap.org` and `oidc.defaultOrg`
keep resolving to it, so a restart does not create a second organisation. Update them when convenient,
not before the rename. The memory tenant (`org:<key>`) never changes. A platform administrator frees an
alias with `DELETE /api/v1/orgs/<slug>/aliases/<alias>`.

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

## HTTPS behind a platform-issued certificate

An instance has **one host**: `global.publicUrl` (the console) and `global.apiUrl` (the API) are
what the API builds everything from, namely the OIDC `redirect_uri`
(`<apiUrl>/api/v1/auth/callback`), the MCP OAuth resource metadata, the commands on the
Integrations page and the links in notifications. Serve it over HTTPS by putting TLS on the
ingress (the certificate comes from the platform, or from cert-manager) and by setting both
URLs to `https://…`. Nothing else follows from the chart: the console's CSP and CORS are derived
from the same two values.

The session cookie and the sign-in handshake cookie are `Secure` when `CHOREGOS_ENV` is
`staging` or `prod`, **or when `publicUrl` or `apiUrl` starts with `https://`**, in any
environment. A dev tenant served over HTTPS therefore never sends its session cookie in clear;
a bench on `http://localhost` keeps working because its cookie is not `Secure`. A browser only
returns a `Secure` cookie over HTTPS, so a deployment whose URLs say `https` but whose ingress
still answers on plain HTTP cannot sign in: redirect HTTP to HTTPS at the ingress. When you move
a deployment to a new host, register the new `<apiUrl>/api/v1/auth/callback` at the identity
provider before switching, and keep the old one until the old host only redirects.

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

The same claim carries an organisation's **human groups** (ADR 0052): a workflow's
`group: architects` is matched by a member carrying `architects` or `choregos:<org>:architects`
(the one mapper prefix `choregos:<org>:` serves roles and human groups). A group namespaced for
another organisation never counts. The groups are stored as sent.

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

**pgvector, for the knowledge index** ([ADR 0051](adr/0051-knowledge-bases.md)). A knowledge base
served as an index ranks with full-text search and, when the `vector` extension exists in the
application database, with embeddings too (`hybrid`). The extension is not trusted, so the
application role cannot create it: create it as a superuser — CloudNativePG's
`bootstrap.initdb.postInitApplicationSQL` (not `postInitSQL`, which runs in the `postgres`
database); the embedded chart's image (`pgvector/pgvector:0.8.1-pg16`) does it at initdb. Without
it nothing breaks: the index serves full-text only and the knowledge page says so. For an existing
database: `CREATE EXTENSION vector;` as a superuser, then `ALTER TABLE knowledge_chunks ADD COLUMN
embedding_vec vector;` as the owner of the schema, then **reindex** each index base.

**Checking the sign-in configuration from the console.** *Admin → Platform* shows a read-only card,
"How people sign in to this instance", for the platform administrator: the OIDC issuer, client id and
discovery URL, whether the client secret is set (never its value), the redirect URI to register at the
identity provider (`<apiUrl>/api/v1/auth/callback`), the MCP door's OAuth issuer, audience and registered
clients, the session lifetime, the development login switch, and the bootstrap organisation and the
number of bootstrap administrators (never their e-mails). It is the answer of
`GET /api/v1/platform/identity`. Nothing is editable there: the values come from the chart
(`global.oidc`, `global.mcp.oauth`), and a row marked `default` was not set by the deployment. When
`oidc.issuer` reads "not configured", the instance still runs on the built-in development issuer.

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

### Traces (OpenTelemetry GenAI, ADR 0018)

Off by default. A process sends spans only when the standard `OTEL_EXPORTER_OTLP_ENDPOINT` (or
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`) is set, over OTLP/HTTP, batched; the other `OTEL_*` variables
of the SDK apply. `global.tracing.otlpEndpoint` sets it on the API, each worker, the migration Job
and the embedded gateway (S40-20b); `choregos-allow-platform` already lets them reach port 4318 in
the collector's namespace. The collector the bootstrap installs (`infra/bootstrap/otel-collector.yaml`) listens
at `http://otel-collector.monitoring.svc:4318`, sends every span to Tempo and derives the span
metrics of the `Choregos — Traces` dashboard. An install that set the variable through
`global.extraEnv` still renders, as long as `global.tracing.otlpEndpoint` is empty.

```yaml
global:
  tracing:
    otlpEndpoint: http://otel-collector.monitoring.svc:4318
    tempoUrl: http://tempo.monitoring.svc:3200   # the nightly ledger-vs-trace job (S40-20c)
```

The network policies open port 4318 and Tempo's query port in the namespace each address names
(`<service>.<namespace>.svc…`), `monitoring` when the host names none (S40-20e). A platform that
already runs Tempo needs no collector of its own: Tempo receives OTLP itself. The Diametral dev
tenant points both values at `tempo.tempo.svc.cluster.local` (ports 4318 and 3200); its chart network
policies are off and the tenant's own policies open those two ports to the API, the workers, the
gateway and the drift job only, never to the agent pods (#946): `choregos-egress-traces-otlp` and
`choregos-egress-traces-query`, applied on dev on 2026-10-26.

Without the collector, the `Choregos — Traces` dashboard reads Tempo's own span metrics (S40-20f):
each panel has two targets, the collector's (`traces_span_metrics_*`, latency in milliseconds,
`service_name`) and Tempo's metrics generator's (`traces_spanmetrics_*`, latency in seconds,
`service`), and an install has data in one of the two. Tempo must run the `span-metrics` processor
with the collector's dimensions, or the panels that break down by model, stage or tool draw nothing:

```yaml
# Tempo's configuration (grafana/tempo chart: `tempo.metricsGenerator.processor`)
metrics_generator:
  processor:
    span_metrics:
      dimensions: [gen_ai.operation.name, gen_ai.request.model, gen_ai.provider.name, project, stage, error.type]
overrides:
  defaults:
    metrics_generator:
      processors: [service-graphs, span-metrics]
```

On the Diametral dev tenant, Tempo is DiametralGroup/infra's `platform/tempo` (infra#1355).

`global.tracing.tempoUrl` renders the CronJob `choregos-trace-drift` (`api.traceDrift.schedule`,
03:17 by default) and a network policy that opens that port to its pods only. Each
night it reads the runs whose spend was collected in the last 24 hours (`api.traceDrift.hours`),
leaving out the last 30 minutes (`settleMinutes`, spans arrive late), and compares each run's model
tokens in the ledger with the usage on the gateway's spans of its trace (input and output, within
`CHOREGOS_TRACE_DRIFT_TOLERANCE`, 1 % of the ledger's figure by default). The ledger is never
corrected (ADR 0004). A drift is a platform audit line `trace.drift` (no organisation, so only a
platform session reads it: `GET /audit` as a platform administrator), written once per run; a run
whose trace has no gateway usage is counted as `missing` and not recorded, since a trace may be
sampled or dropped. The job prints its counts as one JSON line and fails only when Tempo answers
with an error. Run it by hand with `python -m choregos_api.trace_drift --hours 72`.

The agent pods are not given the endpoint, and `choregos-runner-egress` does not open the
collector to them (#946): they run the agent's code, which could otherwise forge the gateway's
spans, and so a false drift, or carry data out past the run's egress proxy. The runner's
`invoke_agent` span is therefore not exported. Relaying it through the API, which would stamp the
run's ids itself, is not decided; do not open the collector in the runner's policy to get it.

The gateway's spans: the embedded LiteLLM loads `choregos_llm_spans` (`litellm_settings.callbacks`),
and each model call made with a run's key becomes `chat <model>` (or `embeddings`,
`text_completion`), a child of the run's stage span, with the provider, the requested and answering
model, the provider's token counts and, on a failure, `error.type`. The run comes from the key's
metadata, never from the request; a call made with a key that names no run has no span.

A run is one trace. Its root is the orchestrator's `invoke_workflow <stage>` span, which lasts as
long as the wait for the run; the runner's `invoke_agent <backend>` span (the agent's session) and
the API's `execute_tool <tool>` spans (each call of the tool route — catalogue, plugin, broker,
`web_fetch`, `web_search`, refused calls included) are its children. The trace and root span ids are
derived from the run id, so the three processes agree without a lookup; the runner receives them in
`StageInput.trace_parent`. Every span carries `run_id`, `project`, `work_item` and `stage`, the keys
of the JSON logs. A span never carries a prompt, a tool's arguments or its result.

Traces are for reading. No gate, budget or decision reads one, and a collector that is down only
loses spans: the ledger stays the record of spend, the run journal the record of what happened.

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
(`http://choregos-demo-fakes:8090/{mdm,shipping,access_control,supplier}/mcp`), and a fake
Microsoft Graph (`…/graph/v1.0`, `…/login`). One replica is the point: the API runs two and the
orchestrator is apart, and in-memory fakes would show each process its own directory. The network
policy opens that pod to the platform; `demoFakes.tokenSecret` (a secret reference) protects it
further. A demonstration: the chart refuses it in `staging` and `prod`. `essai/rh/` plays the HR
scenario against it.

### Which edition am I running?

```bash
curl -s https://<api-host>/edition
{"edition":"community","features":[],"version":"0.4.1","publisher":null,"edition_version":null}
```

`version` is the core's. `publisher` and `edition_version` are announced by the edition's plugin:
on the enterprise edition they say who publishes it and which version of the plugin runs
(`"publisher":"GraalSystems","edition_version":"0.2.0"`), and the admin page and the footer show
them. They are `null` on the community edition, and absent from an older core.

Three more fields feed the console's **About** entry (sidebar, bottom left; the mobile menu has
it too):

| Field | Meaning |
|---|---|
| `licence` | the licence of the core (`Apache-2.0`) |
| `edition_licence` | the licence the edition's plugin announces through `declarer(..., licence=...)`; `null` on the community edition |
| `build.revision` | the source commit the running image was built from, or `null` |

`build.revision` comes from the `CHOREGOS_BUILD_REVISION` environment variable, which the API and
worker images set from their `REVISION` build argument (`--build-arg REVISION=$(git rev-parse HEAD)`;
the CI and release workflows pass the commit). An image built by hand without it reports `null`,
and so does a value that is not a 7 to 40 character lowercase hexadecimal commit id. **`/edition` is
public** (the sign-in page reads it), so the revision is public too: a commit id of an open source
repository, but a deployment that does not want to say which commit it runs sets the variable to
an empty string.

This repository is the **community edition**: Apache-2.0, and **one organisation**. Creating a
second one is refused with a 409 that names the decision behind it
([ADR 0024](adr/0024-two-editions.md)). That is not an arbitrary cap — multi-tenancy is not
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
| `helm template` | `edition: enterprise` with no `global.imagePullSecrets` | the enterprise images live in a private registry; that pull secret **is** the access control in phase 1 ([ADR 0024](adr/0024-two-editions.md), decision 5). Without it the pods sit in `ImagePullBackOff`, which never names the cause |
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
| An agent must not reach the Internet except where the policy says | in **per-project runner namespaces** the provisioning deploys a **Squid proxy per project** with the policy's `allow_domains` (project-level, host-level, no per-agent wall), and every agent pod gets `HTTPS_PROXY` pointing to it. No allowed domain, no proxy, no door. In the **release namespace** (the `agent_sandbox` executor) the chart's **`choregos-egress`** proxy is per run: it knows which run is calling, applies the agent ∩ project ∩ organisation network, and reports every decision ([ADR 0070](adr/0070-network-access-is-bounded-and-enforced-per-run.md)) | `policy.sandbox.network.allow_domains`, `choregos-orchestrator.runner.egressProxy` (empty on a bench), `runner.egressImage` to pin by digest; `global.egress.*` for `choregos-egress` |
| Temporal must be restorable | it holds the position of every ticket in flight; `docs/runbooks/temporal-backup.md` says what to copy and how to reconcile afterwards | — |

The proxy is the honest part of "egress allowlist": before 2026-09-25 the NetworkPolicy
opened a `choregos-egress` namespace that no chart delivered, and the allowlist was an
annotation. It is verified on a cluster (`tests/cluster/test_egress_proxy.py`: an
allowed domain answers 200 through the proxy, any other gets 403 from Squid).

### The per-run egress proxy (`choregos-egress`)

`global.egress.enabled: true` renders a Deployment and a Service named `choregos-egress` in the release
namespace, from the API image (`python -m choregos_api.egress`), with a restricted pod (non-root,
read-only root file system, all capabilities dropped, `RuntimeDefault` seccomp, no service account
token). Nothing is rendered by default.

| Value | Default | What it does |
| :-- | :-- | :-- |
| `global.egress.enabled` | `false` | renders the proxy, makes the orchestrator set `CHOREGOS_RUNNER_EGRESS_AUTH=run` (the runner then presents its run id and run token as proxy credentials) and opens runner pods to it |
| `global.egress.replicas` | `1` | rates are counted per replica: with N replicas a rate is up to N times looser |
| `global.egress.port`, `healthPort` | `3128`, `8081` | the proxy and its `/healthz` |
| `global.egress.ports` | `[443, 80]` | the destination ports a tunnel may open (`CONNECT` and plain HTTP); anything else is refused |
| `global.egress.denyDomains` | `[]` | hosts no run may reach, for the whole deployment: the proxy and the API (`CHOREGOS_NETWORK_INSTANCE_DENY`) both read it |
| `global.egress.cacheSeconds` | `60` | how long a run's network is cached: a revoked agent or a changed ceiling applies within this time |
| `global.egress.resources` | 50m/64Mi, limits 500m/256Mi | |

`choregos-orchestrator.runner.egressProxy` MUST name the Service
(`http://choregos-egress.<release-namespace>.svc:3128`) — the chart refuses to render
`global.egress.enabled` with an empty value (`templates/garde.yaml`): a proxy nobody sends traffic to
would look like a control. The default value, `http://choregos-egress.{namespace}.svc:3128`, resolves to
it when `runner.namespacePattern` is the release namespace.

With `networkPolicies.enabled` the chart goes with it: `choregos-allow-platform` no longer selects runner
pods or the proxy (a runner must not reach PostgreSQL or Temporal); `choregos-runner-egress` lets runner
pods reach only DNS, the API pods, the gateway, the memory service, telemetry and, when the proxy is on,
`choregos-egress` on its port; `choregos-egress-net` takes traffic from runner pods only and lets the proxy
reach the API and the Internet **except the private ranges** (`10/8`, `172.16/12`, `192.168/16`,
`169.254/16`) on the configured ports. The proxy also refuses, by itself, a name that resolves to a
non-public address, and it connects to the address it resolved.

**What it proves, and what it does not.** For a run, it enforces host, port and a *connection* rate
(per host, per run, per organisation host) and journals each decision (`GET
/orgs/{org}/network-activity`). It cannot see inside TLS: method, path, headers and User-Agent are
invisible to it, and one tunnel can carry many HTTP/2 requests. It is a wall only where the network forces
traffic through it.

**Reading a `502`.** `choregos-egress: <host>:<port> is allowed for this run but could not be reached`
means Choregos allowed the host and something *below* it blocked or lost the connection: the node's
network, an egress firewall, a Cilium FQDN rule that does not list the host, DNS. It shows in Network
activity as `unreachable`. A `403` names the reason (`not_allowed`, `denied:<pattern>`, `ip_literal`,
`port_not_allowed`, `private_address`), a `429` comes with `Retry-After`, and a `407` means the proxy
credentials are not a live run's.

**On the Diametral platform** the infra owns egress with Cilium FQDN policies: runner pods may reach eight
hosts directly, identically for every project and agent, so the Choregos proxy is a net there until infra
(1) gives the `choregos-egress` pods an FQDN egress rule for the hosts the instance allows and (2) removes
the runner entry's direct FQDNs. Until then a `502 unreachable` also appears if the proxy pod itself has no
FQDN rule. The deploy repository sets `global.egress.enabled` and `runner.egressProxy` only after that.

## Gateways and what each can do

`/platform/models` and a project that declares no `gateway` connector use the deployment's gateway
adapter (`global.gateway.type`, env `CHOREGOS_GATEWAY_TYPE`), not LiteLLM by default whatever was
configured. The organisation's model catalogue (`/admin/models`, [ADR 0060](adr/0060-a-gateway-agnostic-model-catalogue.md)) is filled from the gateway of the organisation: an organisation connector of kind `gateway` if it has one, else this deployment default. Each adapter declares its `capabilities`:

| Capability | Meaning | `litellm` | `openai-compatible` | `direct` |
|---|---|---|---|---|
| `list_models` | discovers the models it routes | yes | yes (`GET {base_url}/models`) | no (static list) |
| `virtual_keys` | one key per run, limited to its models | yes | no | no |
| `hard_budget` | the key's budget is a cap enforced at the source | yes | no | no |
| `spend_readback` | the spend of a key is read back | yes | no | no |
| `teams` | a project's day budget held by the gateway | yes | no | no |
| `pricing` | the catalogue carries prices | yes | no | no |

With `openai-compatible` (Portkey, Kong AI Gateway, Envoy AI Gateway, OpenRouter, a vLLM router...)
the budget is enforced by Choregos only (the reservation, turns and minutes): there is no cap at
the source and no measured cost. Configure two secrets, `api_key` (discovery) and `run_key` (what
runs receive, the same for every run: scope it at the gateway with its own rate limit).

A LiteLLM you run yourself (`global.gateway.embedded: false`, type `litellm`) is the platform's
`litellm` gateway: the API mints marked keys for it and expects the secret guard on the path. Load
it as the embedded one does: put `charts/choregos/charts/litellm/files/choregos_guardrail.py` next
to LiteLLM's `config.yaml` (same release, byte for byte) and declare it in that config:

```yaml
guardrails:
  - guardrail_name: choregos-secrets
    litellm_params:
      guardrail: choregos_guardrail.ChoregosGuardrail
      mode: [pre_call, post_call]
      default_on: true
```

Without it, a key the platform marked `choregos_llm_guard: redact` reaches the provider unmasked,
and nothing fails to say so. `GET /guardrails/list` with the master key shows `choregos-secrets`
when the guard is loaded. LiteLLM reads its config only at start: restart it after the change.
