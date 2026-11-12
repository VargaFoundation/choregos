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

Since 0.27 the API also deletes objects (the bytes of a task artifact when a person or a subject is erased,
and when the retention purge removes an expired one): the store's credentials need `s3:DeleteObject` on the
prefix. Without it an erasure of an assistant's data that holds a file artifact answers 502 and erases
nothing, and the purge keeps the expired rows and retries them at every pass.

An empty `endpoint` means AWS, and without `secretRef` the calls are unsigned: nothing fails at install, and
the first run that writes a file loses its run. The project's readiness (`object-store` item, `docs/usage.md`)
probes the bucket when a workflow declares a file output and names the cause.

Turning all four on gives a self-contained bench on a single node:
`-f charts/choregos/values/local.yaml`.

## Secrets

The chart never writes production secrets. It expects them to exist:

| Secret | Keys | Used by |
| :-- | :-- | :-- |
| `global.apiSecretRef` (default `choregos-api-secrets`) | `run-token-private-key`, `github-app-id`, `github-app-private-key` (the last two optional when the App is created from the console, [the GitHub App](#the-github-app)) | API **and** orchestrator |
| `global.apiSecretRef`, entries `session-secret`, `run-token-public-key`, `github-webhook-secret` | the session signing key (required, see below), the run-token public key (derived from the private one when absent), the GitHub webhook secret | API only |
| `global.apiSecretRef`, entry `session-secret-previous` (optional) | the previous session key during a rotation, `CHOREGOS_SESSION_SECRET_PREVIOUS`: verification only | API only |
| `global.apiSecretRef`, entries `github-app-client-id`, `github-app-client-secret` (optional) | the App's OAuth client: needed only to claim an installation when the instance has several organisations ([the GitHub App](#the-github-app)) | API |
| `global.apiSecretRef`, entry `secrets-keks` (optional) | `CHOREGOS_SECRETS_KEKS`, see below | API **and** orchestrator |
| `global.apiSecretRef`, entry `egress-report-key` (optional) | `CHOREGOS_EGRESS_REPORT_KEY`: the egress proxy's own key, a random string (48 characters or more). Its reports need it on top of the run's egress token, so an agent cannot rewrite its run's network counters ([ADR 0145](adr/0145-the-proxy-reports-with-a-key-the-agent-does-not-hold.md)); absent, the API logs `egress.report_key_missing` at start-up and accepts the egress token alone. Rotate it by replacing it and restarting the API and `choregos-egress` together | API **and** egress proxy |
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

**The session secret** (`session-secret`, `CHOREGOS_SESSION_SECRET`) signs the session cookie and the
sign-in handshakes, and only the API reads it: the workers and the migrations Job never need it. It
is required. The API refuses to start when it is empty, whatever the environment, and on any deployed
instance (`global.environment` staging or prod, or a public or API URL served over https, whatever
`global.environment` says) when it is shorter than 32 bytes or is the default published in the
repository. The error names the variable. Generate one with:

```bash
openssl rand -base64 48
```

Each value signed with it carries a 256-bit tag bound to its purpose (session, OIDC state, OIDC
handshake, remote OAuth state and handshake, GitHub App creation state): a value signed for one
purpose is refused for another.
The API still reads the session cookies issued by the previous release for one more release; they
carry a 128-bit tag without a purpose and are accepted as sessions only.

**Sessions are recorded** (ADR 0130): each sign-in writes a `user_sessions` row, and the cookie names
it (`sid`). A person sees their sessions on *My account → Sessions* and ends one, or all the others;
signing out ends the session on the server too. A cookie whose session was ended is refused at its next
request. Cookies issued before the upgrade carry no `sid`: they hold until they expire (8 hours at most),
unlisted, unless their owner ends their other sessions.

To rotate it, move the old value to `session-secret-previous`, put the new one in `session-secret`,
redeploy, and remove `session-secret-previous` once `session_max_age_s` (8 hours) has passed: until
then a session signed with the old key still signs its holder in, and nothing new is signed with it.
On a deployed instance the previous key must also be at least 32 bytes. With an ExternalSecret, set
`choregos-api.externalSecrets.sessionSecretPrevious: true` once the vault holds the property (a
missing property fails the whole ExternalSecret sync). Do not pass it through `global.extraEnv`: a
`CHOREGOS_SESSION_SECRET_PREVIOUS` there fails the render, because the API chart already sets it. The
sign-ins in flight at the deploy that adds the purposes (10 minutes at most) fail once and are
started again.

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
[the runbook](runbooks/secret-rotation.md#connector-secrets-key-secrets-keks)) before dropping the
old key, and drop it only once no database backup older than the rewrap can still be restored. `global.secrets.keksInExternalSecret: true` lists the entry in the chart's `ExternalSecret`
(turn it on once the key exists in the store: a missing remote property fails the whole object).

`global.devSecrets: true` makes the chart generate all of the above itself, the secrets key
included (drawn once, then re-read). It exists for a
bench and says so; never use it in production.

**Connector secrets by `env:NAME`.** Only for a key the operator owns: an organisation's own key (a
market-data key, a tracker token) is typed in the console and sealed under `secrets-keks`, never mapped
into the pods here, where any organisation's connector could name it (S39-06e). A secret a connector references (`secret_refs: {api_token:
env:CONNECTOR_JIRA_TOKEN}`) must be mapped into the API and worker pods (`global.extraEnv` with
`valueFrom: {secretKeyRef: …}`, from a Secret) under a name matching `global.secrets.envPattern` —
by default `^CONNECTOR_[A-Z0-9_]+$`, set as `CHOREGOS_SECRET_ENV_PATTERN`. Widen the pattern for your own naming; `CHOREGOS_*`,
`DATABASE_*`, `PG*`, `AWS_*`, `KUBERNETES_*` and `TEMPORAL_*` stay refused whatever it says. After
an upgrade, a stored reference outside the pattern stops resolving (the connector shows in error):
rename its variable or widen the pattern.

## The GitHub App

One App serves the deployment: the tracker, the pull requests, the checks and the run's git token go
through it, and whoever registers it on GitHub owns it. It comes from one of two places, and the first
wins ([ADR 0161](adr/0161-the-github-app-is-created-from-a-manifest.md)):

1. **The deployment's settings**: `github-app-id` and `github-app-private-key` (with
   `github-webhook-secret`, and `github-app-client-id` / `github-app-client-secret` for claims on an
   instance with several organisations) in `global.apiSecretRef`. When they name an App, it is the App.
2. **An App created from the console** (*Administration → GitHub App*), registered on github.com from a
   manifest the platform builds and stored sealed in the database. Only when the settings name none.

*Administration → GitHub App* says which one the instance acts as (`GET /api/v1/platform/github-app`).

### Create it from the console (GitHub App Manifest flow)

For a platform administrator, on an instance whose deployment names no App. It needs the secrets key
(`secrets-keks`, [below](#secrets)): the App's private key, webhook secret and client secret are sealed
with it, and without it the page refuses before anything is registered on GitHub.

1. *Administration → GitHub App*. Check the name (unique across GitHub, 34 characters at most; the page
   proposes one from the instance's host).
2. **Owner**: leave it empty for your personal account, or name a GitHub organisation you are an owner
   of. The owner owns the App on GitHub: choose the account of whoever operates this instance.
3. **Who may install it**, a choice with no default: **Private** installs only on the owner's account
   (enough when every repository belongs to it); **Public** lets any GitHub account install it, as each
   company using a shared instance does on its own account. The owner can change it later in the App's
   settings on GitHub.
4. Untick **GitHub can reach this instance** when GitHub cannot reach `<api_url>` (an instance behind a
   VPN): the webhook is registered inactive, and installations are still discovered by polling.
5. **Continue on GitHub**: GitHub shows the App it is about to register; confirm. It sends you back to the
   console within the hour (`?github_app=created`), and the App is the deployment's App from then on.
   Another replica of the API learns of it within a minute.
6. Install it on the repositories from a project's connectors (*Install on GitHub*), as below.

What the platform puts in the manifest, derived from the instance and the adapters, never typed:

| Manifest key | Value |
| :-- | :-- |
| `default_permissions` | Repository: `contents` write, `issues` write, `pull_requests` write, `checks` write, `metadata` read, `statuses` read; organisation: `organization_projects` write |
| `default_events` | `issues`, `issue_comment`, `projects_v2_item`, `pull_request`, `pull_request_review`, `check_suite`, `check_run` (`installation` and `installation_repositories` are delivered to every App; nobody subscribes to them) |
| `hook_attributes.url` | `<api_url>/api/v1/webhooks/github` |
| `callback_urls` | `<api_url>/api/v1/github/setup`: where GitHub sends a person after an installation |
| `request_oauth_on_install` | `true`: the person who installs authorises the App, the proof a claim needs on an instance with several organisations (no Setup URL then: GitHub uses the callback URL) |
| `redirect_url` | `<api_url>/api/v1/github/manifest/callback`: where GitHub sends the browser once the App is registered |
| `url` | `<public_url>`, the App's homepage |
| `public` | as chosen |

The permissions and events are the adapters' own list (`APP_PERMISSIONS`, `APP_EVENTS` in
`choregos_adapters.github`), the one the GitHub procedures show; a test holds this table to it.
Add `workflows: write` on GitHub by hand only if agents edit `.github/workflows`.

**What is stored where.** One row of `github_app_registrations`, readable in the platform scope only:
the App's id, slug, name, owner, address, public flag and client id in clear, and one envelope sealed
under the active key of `CHOREGOS_SECRETS_KEKS` with the private key, the webhook secret GitHub
generated and the client secret. Nothing is written to the Kubernetes Secret. Creating and forgetting
are audited (`github_app.create`, `github_app.forget`).

**Rotating and forgetting.** `choregos admin secrets rewrap` moves the App's envelope to a new key with
the connector secrets. *Forget this App* deletes the stored credentials; the App stays on GitHub, where
its owner deletes it (or keeps it and puts its id and a new private key in the deployment's settings,
which then win). Losing the secrets key loses the App's private key: generate a new one on GitHub and set
it in the deployment's settings, or forget the App and create another.

### Create it by hand

Register the App on github.com (*Settings → Developer settings → GitHub Apps → New GitHub App*, under the
account that will own it) with the values of the table above, generate a private key and a webhook secret,
and put `github-app-id`, `github-app-private-key` and `github-webhook-secret` (and, with the OAuth option,
`github-app-client-id` / `github-app-client-secret`) in `global.apiSecretRef`:

| Setting | Value |
| :-- | :-- |
| Permissions and events | As the manifest's table above |
| Webhook URL | `<api_url>/api/v1/webhooks/github` (needs a host GitHub can reach; discovery does not depend on it) |
| Where can this GitHub App be installed? | **Any account** if companies other than the App's owner use the deployment (a private App installs only on its owner's account) |
| Request user authorization (OAuth) during installation | **Tick it before a second organisation exists**, generate a client secret, and give `github-app-client-id` / `github-app-client-secret` to the API. Its **Callback URL** is `<api_url>/api/v1/github/setup` |
| Setup URL | Without the OAuth option only: `<api_url>/api/v1/github/setup`, with **Redirect on update** ticked. GitHub offers no Setup URL once the OAuth option is ticked |

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
| `aca` | Azure Container Apps job | Verified against a real subscription; runs outside the cluster, so it needs a [run door](#the-runs-api-and-the-run-door) |
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

An image in a private registry project pulls with the same `global.imagePullSecrets` as the chart's
own pods (S51-09): the chart puts them on the `choregos-runner` ServiceAccount and hands their names
to every worker (`CHOREGOS_RUNNER_IMAGE_PULL_SECRETS`), and the `k8s_job` executor names them in each
runner pod it creates — a job step's image and a hosted agent's container alike. Each secret must
exist in the namespace the runner pods run in (`runner.namespacePattern`).

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
enterprise feature: an extension calls `choregos_api.extensions.declare_theme_provider(fn)` with
`fn(session, org) -> dict | None` and the core validates what it returns (schema, then the
console's contrast budget on the effective colours). An invalid theme is replaced by the default
(`X-Choregos-Theme: fallback`, with the violations in the API log); an extension can check a theme
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
    mountPath: /etc/choregos/tools
    fileName: catalogue.yaml
    credentialsSecret: choregos-tool-keys   # provider keys, injected into the API
```

Playbooks let a deployment replace **any** role prompt, including `implement`, without
touching the platform — that is what makes the engine usable outside software.

The tool catalogue declares third-party capabilities the platform uses **on behalf of** an
agent, with the provider credential staying server-side. Two kinds of entry:

```yaml
tools:
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
| everything else | **the runner itself** (`runner/local_tools.py`), which starts the server when the announced address is local and nothing answers |

A sidecar, where one exists, keeps the port: the runner probes first and stands aside. A **non**-local
address is served by nobody automatically — that is a deployment providing its own server, and the
runner deliberately abstains.

Two things have to be true for an agent to actually get a tool, and both are easy to miss:

1. the deployment declares it in the catalogue (`global.toolCatalog.configMap`);
2. **the project declares that it uses it** (`tools:` in its configuration). The catalogue says
   what exists; the project says what it draws on. A tool that is in the catalogue and not in the
   project's list comes back `allowed: false`, and the agent correctly reports that it has no such
   tool — which is exactly what happened on the dev tenant on 2026-09-27, and cost an hour.

### The tool bridge

When a run is offered tool scripts (`tools.scripts` in its stage input,
[ADR 0122](adr/0122-an-agent-may-call-its-run-s-tools-from-a-script.md)), the server on `localhost:7777` also
serves the **tool bridge** (`choregos_tools_mcp/bridge.py`): a script the agent writes calls the run's tools
through it, holding no credential of its own.

| Route | What it does |
|---|---|
| `GET /bridge/v1/tools` | the run's tool list as `GET /internal/runs/{id}/tools` answers it, whole (`inputSchema`, `outputSchema`, `x-choregos-access`) |
| `POST /bridge/v1/tools/{name}` | the body is the arguments (empty means `{}`), relayed to `POST /internal/runs/{id}/tools/{name}` with the run token, `X-Choregos-Tool-Origin: script` and the request's `X-Choregos-Script` (64 lowercase hexadecimal characters, else `400`); the API's answer comes back unchanged, status and bytes: a `200` carrying `{status_code, result, remaining, suspicions}`, or the API's problem with its own status (`404`, `409`, `429`, `400`) |
| `POST /bridge/v1/scripts` | `{"sha256", "text"}`: the script's text, journaled once per digest (`201` the first time, `200` after); a text over 64 KiB or a digest that is not the SHA-256 of the UTF-8 text is a `400` |

The bridge adds no rule: the API governs each call as it governs the model's. It bounds what it relays: at most
`max_concurrent_calls` (1-16, default 4) calls of the run at once, the next one waiting for a place rather than
refused; 60 s per call (a governed write waits up to 30 s on the API's side), after which it answers `504`; `502`
when the API cannot be reached; `413` for a request body over 1 MiB. Without `tools.scripts` the routes answer
`404`, as routes that do not exist.

It journals `tools.script` (`sha256`, `bytes`, `text`) and, for each relayed call, `tools.script.call` (`tool`;
`status_code`, the answer's own on a `200`, `202` for a proposal, the HTTP status otherwise; `duration_ms`;
`response_bytes`, what it handed back; `script_sha256`, or null; `action`, the id of the governed action a `202` proposal
created, when the API named one). The runner's own server writes them into the
run's journal and says `bridge: true` on its `tools.local` event. The Tekton sidecar is not told its run's input:
it reads it from the API (`GET /internal/runs/{id}/input`, with the run token) on the bridge's first request, and
posts its events through `POST /internal/runs/{id}/events`, numbered from 1,000,000,000 so that they never take
a number of the runner's. The run's journal is ordered by number, so the sidecar's events come after the
runner's; each carries its own time.

The runner also writes the client a script imports (S57-08, [ADR 0122](adr/0122-an-agent-may-call-its-run-s-tools-from-a-script.md)
§3): `choregos_tools` under `$TMPDIR/choregos/tools`, outside the workspace and anew for each run, from the list
`GET /bridge/v1/tools` hands back (the runner reads it with its own client, so the same list whether it or the
Tekton sidecar serves the bridge); the package's code is `packages/runner/src/choregos_runner/tool_client.py`,
copied, and only the per-server modules and the index are generated. The agent's environment gains
`CHOREGOS_TOOLS_DIR` (that directory), `CHOREGOS_TOOLS_BRIDGE` (`http://<the tool server's host:port>/bridge/v1`),
`PYTHONPATH` (the directory first, an existing value kept after it) and `TMPDIR` (the runner's temporary
directory, which the confinement lets the agent write), for every shell backend; its first prompt gains the
section "Calling tools from a script", after the agent's instructions and before the task (S57-11b), and the
journal says `tools.instructions` (`included`: the prompt holds it, `bytes`: its size). The runner creates `$TMPDIR/choregos/scripts`, where that section tells the agent
to write its scripts: its write guards (`GuardRails.check_write`, Claude Code's `check_scope.sh`) and the ACP
client's file system let the agent's file-write tool write beneath it — no `.` or `..` segment, no
secret-looking file —, and nowhere else outside the workspace, the client's directory included (#1365). The run's journal says `tools.client` (`written`, `tools`, `modules`, and
`error` when the tool list could not be read: the client is then written without modules, `call` reaching a
tool by its name); a client that cannot be written (`written: false`) leaves the run without it and without
the section (and without `tools.instructions`). A run that is not offered scripts gets none of it.

No new port and no new credential: the routes are on the port the agent's MCP client already uses, so the
confinement's port list is the same with and without scripts, and the run token stays with the runner (or the
sidecar), in no answer and no journal line. Each relayed call holds one API database connection for its whole
length (#1354): a run that scripts can hold `max_concurrent_calls` of them at once, besides its model's own calls.

### A catalogue tool that may write is not offered

Writes reach a third party only as governed actions (ADR 0034 §7), and a catalogue call is not one.
A catalogue tool **may write** when it is an http tool whose method is not `GET`, or a remote `mcp:`
tool. Unless the deployment declares `read_only: true` on the entry, such a tool is not listed in the
run's tool list, is shown `allowed: false` on the project's tools page, is left out of the observability
sources, and a direct call answers 409 `catalogue_write_refused` before any provider call or cost line.
`read_only: false` says the same as leaving it unset on a writer. The catalogue still loads: an old
catalogue does not stop the API from starting.

- A POST or MCP tool that only reads (a search, a lookup): add `read_only: true` to its entry.
- A tool that writes: declare the server as an organisation MCP connector. The broker serves its
  operations, each with its own policy, and its writes are governed. The tools of a plugin's server
  (ADR 0096) are such connectors, not catalogue entries, and are not affected by this rule.

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
and `demo/tools/catalogue.yaml` for a working example that needs no key at all.

The catalogue's top-level key is `tools:`. The former key was removed in 0.24.0: a catalogue that
still uses it is refused (see "Upgrading to 0.24").

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

## An MCP gateway (ADR 0105)

An MCP gateway is another product, never part of this chart
([ADR 0105](adr/0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md)). With the default
NetworkPolicies on, the chart still has to let the platform reach the one you run. The
`global.mcpGateway` block does that, and nothing else; a default render changes nothing.

| Key | Effect |
|---|---|
| `internalHosts` | Exact host names, rendered as `CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS` (a JSON list) on the API and the workers. The API refuses them as a run's destination, and the platform may call them. |
| `hosts` | Appended to the `api` and `workers` families of `global.egress.ceiling`, only when you declared those families (an undeclared family stays *unknown*; `runners` is never touched). |
| `namespace`, `podLabels` | In-cluster gateway: the policy `choregos-mcp-gateway-egress` opens that namespace (and those pod labels). |
| `cidrs` | External gateway: one `ipBlock` per CIDR. |
| `ports` | TCP ports opened (default `[443]`). |

The policy is rendered when `namespace` or `cidrs` is set. It selects the API and orchestrator
pods only: the API calls the gateway to list tools, and an approved write runs in an orchestrator
worker. **Never the runners**: a run is untrusted code, and the gateway holds the credentials a run
must not reach, so the runner policy is unchanged and the API also denies the gateway's hosts to
runs. A plugin's server is run by the platform in-cluster, is never reached through the MCP gateway
and needs nothing from `global.mcpGateway` (see [Plugin servers](#plugin-servers)).

`internalHosts` is a separate list from `global.egress.denyDomains` on purpose: `denyDomains` also
bounds the platform's own calls, which must reach the gateway. Runs are kept off it by the API.

In-cluster gateway:

```yaml
global:
  mcpGateway:
    internalHosts: [gateway.mcp.svc.cluster.local]
    namespace: mcp
    podLabels: {app: gateway}
    ports: [8080]
```

External gateway:

```yaml
global:
  egress:
    ceiling:
      api: [api.github.com]
      workers: [api.github.com]
  mcpGateway:
    internalHosts: [gateway.example.test]
    hosts: [gateway.example.test]
    cidrs: [203.0.113.0/24]
```

## Outposts (ADRs 0147-0149)

An outpost is a place an organisation's administrator enrols, which only dials out to the platform
([ADR 0147](adr/0147-an-outpost-only-dials-out.md)): the platform never connects to it and stores no
address of it. Outposts are off unless you turn them on. What is in place is the platform's side of
the record (the tables, the `outpost:manage` right of the organisation's administrator and the
management API, `/api/v1/orgs/{org}/outposts…`), the door an outpost dials (`/outposts/v1`,
[ADR 0148](adr/0148-the-outpost-door-is-a-third-door-with-its-own-contract.md)): an instance enrols
there, is approved by a person, and polls; and the connector type that routes a tool call to one
(`mcp-outpost`, [ADR 0149](adr/0149-a-tool-may-live-at-an-outpost.md);
[Integrations](integrations.md#a-tool-server-at-an-outpost-adr-0149)). The daemon is a separate package
with a chart of its own (below).

This section holds the settings and the values. When to want an outpost, and when an organisation's
own MCP gateway serves better; what an outpost gives and what it does not; and each gesture of its
life, from the enrolment to the rotation of the door's key, are in [Outposts](outposts.md). The
guide for whoever runs an outpost in their own network is [Running an outpost](outpost-operator.md).

| Setting | Default | What it does |
| :-- | :-- | :-- |
| `CHOREGOS_OUTPOSTS` | `off` | `on`: the door is mounted, outposts can be created and enrolment tokens issued. `off`: the door is not served at all (a daemon meets a plain 404), creating an outpost or a token is refused (409 `outposts_disabled`), and what exists can still be listed, revoked and deleted. It is the one switch that stops every outpost. Read when the API starts: changing it takes a restart |
| `CHOREGOS_OUTPOSTS_DOOR_URL` | empty | the public origin of the outposts' door: a scheme and a host, no path (`https://outposts.example.com`). It is returned with each enrolment token as `control_plane`, which is what the outpost's operator writes in the policy file; a request an outpost signs binds its URL under that origin (`packages/contracts/outpost-door.yaml`). Give the door a hostname of its own: it is the one name an outpost's firewall opens |
| `CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY` | empty | **a secret.** The Ed25519 private key (PEM, PKCS#8) the door signs every call it hands over with ([ADR 0148](adr/0148-the-outpost-door-is-a-third-door-with-its-own-contract.md) §13). Only its public half is ever shown. Without it no enrolment token is issued (409 `door_key_missing`): an outpost makes only calls signed by a key it pins, so an instance enrolled with nothing to pin could never be handed anything |
| `CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS` | `[]` | public keys shown beside the one that signs, for a rotation, as a JSON list; three at most, since a policy file pins four keys. Each is the unpadded base64url of the key's 32 raw bytes |
| `CHOREGOS_OUTPOSTS_DOOR_RATE_PER_MINUTE` | `1200` | the most requests one instance sends the door in a minute, counted in memory and per replica: beyond it the door answers 429 `rate_limited` with `retry_after`. `0` lifts the limit. It bounds an instance that authenticated; requests that carry no valid signature are bounded at the entrance, by source |
| `CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_OUTPOST` | `128` | the most calls one outpost has in flight (placed, and not yet answered, refused or out of time). One more is refused where it is admitted, before anything is written |
| `CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_ORGANISATION` | `512` | the same bound over all the outposts of one organisation |
| `CHOREGOS_OUTPOSTS_CALLS_PER_MINUTE_PER_OUTPOST` | `600` | the most calls one outpost is handed in a minute: the calls placed in the last sixty seconds, counted from their rows where the next one is admitted, so every replica reads the same number. One more is refused before anything is written, and the caller is told to come back (a tool error `outpost_not_admitted`, 429) |
| `CHOREGOS_OUTPOSTS_CALLS_PER_MINUTE_PER_ORGANISATION` | `2400` | the same bound over all the outposts of one organisation |

**With the chart.** `global.outposts` renders these settings, the door's route and, if you ask for it,
the door's own Deployment. Nothing of it is rendered while `global.outposts.enabled` is false, which
is the default.

```yaml
global:
  outposts:
    enabled: true
    callsInFlight: { perOutpost: 128, perOrganisation: 512 }
    door:
      hostnames: [outposts.example.com]   # the door's own; the origin is https://<the first one>
      signingKey:
        existingSecret: outposts-door-key # empty: the chart draws one, once
        key: signing-key
      announcedKeys: []                   # for a rotation
      ratePerMinute: 1200
      dedicated: false                    # true: a Deployment that serves the door alone
```

| Value (under `global.outposts`) | Default | What it renders |
| :-- | :-- | :-- |
| `enabled` | `false` | `CHOREGOS_OUTPOSTS=on` for the API and for every worker (whoever places a call reads the switch), the door's settings for the API, the door's route, and the alert rule below |
| `callsInFlight.perOutpost`, `.perOrganisation` | `128`, `512` | the two quotas, for the API and the workers |
| `door.hostnames` | `[]` | the hostnames of the route `choregos-outposts-door`, which serves `/outposts` and nothing else, in the entry form of `choregos-api.ingress` (an HTTPRoute or an Ingress, the API's class and gateway). A hostname that is also the console's (`choregos-web.ingress.hostnames`, `global.publicUrl`), the API's (`choregos-api.ingress.hostnames`, `global.apiUrl`) or a run door's fails the render: the one name an outpost's firewall opens is a name of its own |
| `door.url` | empty | `CHOREGOS_OUTPOSTS_DOOR_URL`. Empty: `https://` and the first hostname. Enabled with neither a hostname nor a URL, or with a URL that has a path, the render fails and names the value, before the API would refuse to start |
| `door.signingKey.existingSecret`, `.key` | empty, `signing-key` | the Secret the API (and the door's Deployment) reads `CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY` from, by reference and not optional. Empty: the chart's own Secret, below |
| `door.announcedKeys` | `[]` | `CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS`. A key that is not one, a key listed twice or a fourth one fails the render |
| `door.ratePerMinute` | `1200` | `CHOREGOS_OUTPOSTS_DOOR_RATE_PER_MINUTE`; `0` lifts the limit |
| `door.requestTimeout` | `60s` | the HTTPRoute's `timeouts.request` (Gateway API v1.2 and later); empty leaves the field out |
| `door.annotations` | ingress-nginx's read timeout and buffering | the door Ingress's own annotations: its timeout, and its rate limit or source restriction |
| `door.dedicated` and what follows it | `false` | the door's own Deployment, below |

`global.extraEnv` may not set one of these variables once `global.outposts.enabled` is true: the
render fails and names it. While it is false, an installation that passed them through
`global.extraEnv` (the way before this chart rendered them) renders as before.

**The workers read the switch too.** A governed action's effect that calls a connector at an outpost
is played by the orchestrator, which places the call itself: with the switch off there, such an effect
is refused `outpost_paused`. The chart gives the switch and the quotas of calls in flight to every
worker for that reason (and the workers already hold `CHOREGOS_SECRETS_KEKS`). The workers hold no
`LISTEN` connection: a call a worker placed is read at its next look, up to 10 seconds after the
outpost answered. The two caps per minute (`CHOREGOS_OUTPOSTS_CALLS_PER_MINUTE_PER_OUTPOST`, 600, and
`CHOREGOS_OUTPOSTS_CALLS_PER_MINUTE_PER_ORGANISATION`, 2400) are not values of the chart yet: set them
with `global.extraEnv`, for the API and the workers alike.

**The signing key's Secret.** Either you create it and name it:

**Writes through an outpost** are played by the workers, delivered at most once, and may leave their
action `in_doubt` ([Security](security.md#a-connector-at-an-outpost)). A worker that stops between an
outpost's claim and the record of its answer loses nothing for fifteen minutes: the retry reads the
answer where the door stored it. After that the answer is gone and the action ends in doubt rather
than being sent again. Watch the actions in doubt (`GET /api/v1/projects/{id}/actions?status=in_doubt`,
or the `choregos.action.failed` events whose `status` is `in_doubt`): a count that rises says the link
between an outpost and the platform loses answers. Upgrade the API and the workers together before an
administrator opens a write: a worker of an earlier version places no write through an outpost, and
the action fails saying so. The upgrade adds two columns to `connector_operations`
(`20261112_1300_operation_delivery_statements.py`), null and false on every existing row.

```bash
openssl genpkey -algorithm ed25519 -out door.pem          # PEM, PKCS#8: CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY
kubectl -n <namespace> create secret generic outposts-door-key --from-file signing-key=door.pem
openssl pkey -in door.pem -pubout -outform DER | tail -c 32 | basenc --base64url | tr -d '='   # its public half
```

or you leave `door.signingKey.existingSecret` empty and the chart draws a key at the first
installation, in the Secret `<release>-outposts-door`. That Secret is read back at every upgrade
(Helm's `lookup`), is `immutable`, and carries `helm.sh/resource-policy: keep`: no upgrade changes
the key, and an uninstall does not delete it. Every outpost pins its public half, so back it up.
`lookup` needs the cluster. **Under Argo CD, or any `helm template | kubectl apply`, the chart
cannot read the Secret back and would draw a new key at every render**; the immutable Secret makes
that apply fail instead of changing the key under every outpost. There, create the Secret yourself.

**The keys an operator pins.** `GET /api/v1/orgs/{org}/outposts/{slug}` and the answer that issues an
enrolment token both carry `door_keys`: the public half of the key that signs, then the announced
keys. Whoever runs the outpost copies them into the `door_keys` of its policy file, which is the only
place an outpost reads them from: nothing the platform sends later adds a key to that list or changes
it. `door_keys` is empty while the deployment has no signing key.

**Rotating the signing key** is an overlap that the operators of outposts take part in, in this order:

1. generate the next key and put its public half in `CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS`. The
   management API now shows both keys; each operator adds the new one to the policy file;
2. once every outpost pins it, make it `CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY` and move the former public
   key to the announced list. By the door's contract, an outpost that did not pin the new key makes
   none of the calls signed with it and refuses each with `signature_refused`;
3. when the operators have dropped the former key from their files, remove it from the announced list.

With the chart, each step is a change of values, and the key itself never changes in place: a Secret
is one key for its whole life (the chart's own is immutable).

1. Create a **second** Secret with the next key (the three commands above, under another name, say
   `outposts-door-key-2`), and set `global.outposts.door.announcedKeys` to its public half. Upgrade:
   the API restarts and shows both keys.
2. When every outpost pins the next key, set `global.outposts.door.signingKey.existingSecret` to
   `outposts-door-key-2` and `announcedKeys` to the FORMER public half. Upgrade: the door signs with
   the next key. The former Secret is no longer read; the chart's own one stays in the namespace,
   since it is kept. Leave it there until step 3 is done: it is the way back.
3. When the operators have dropped the former key, empty `announcedKeys`, upgrade, and delete the
   former Secret.

An outpost whose operator uses the `choregos-outpost` chart follows by adding the next key to
`policy.door_keys` at step 1 and removing the former one after step 3: each change restarts its pods.

The API refuses to start on a door URL that is not an origin, on a signing key that is not an Ed25519
private key in PEM, and on an announced key that is not a public key in that spelling, is listed
twice, is the public half of the signing key, or is a fourth one
(`apps/api/tests/test_outposts_settings.py`). With `CHOREGOS_OUTPOSTS` on it also refuses to start
without `CHOREGOS_OUTPOSTS_DOOR_URL` or without `CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY`, naming the one
that is missing: a door could not compare the URL a request signs without the first, and no outpost
would make a call it handed over without the second (`apps/api/tests/test_outpost_door.py`).

**The door.** `/outposts/v1` is served at the root of the API, beside `/mcp` and `/a2a` and outside
`/api/v1` and the OpenAPI document: four `POST` routes, `enrol`, `poll`, `calls/{id}/answer` and
`calls/{id}/refuse` (`packages/contracts/outpost-door.yaml`). What whoever runs the platform has to
arrange for it:

- **A hostname of its own**, the one `CHOREGOS_OUTPOSTS_DOOR_URL` names, routed to `/outposts` and to
  nothing else. It is the one name an outpost's firewall opens (port 443, outbound), and the one you can
  rate-limit or restrict by source apart from the console's. The door compares the URL each request
  signs with that origin, never with the `Host` header: behind a proxy that rewrites the host, nothing
  changes. The chart renders this route from `global.outposts.door.hostnames`
  (`tests/charts/test_outpost_door_routed.py`); the DNS record and the certificate of that hostname
  are yours to arrange, as the console's are.
- **Long requests.** A poll waits up to 25 seconds before it answers with nothing. Give the route an
  idle timeout above that (30 seconds or more), and do not buffer the answer. The chart's HTTPRoute
  carries `timeouts.request` (60 seconds); its Ingress carries the door's own annotations, whose
  defaults are ingress-nginx's: under another controller, write its equivalents in
  `global.outposts.door.annotations`. While a poll waits it
  holds no database connection: a hundred waiting instances cost a hundred open requests and no pool
  (`apps/api/tests/test_outpost_long_poll_holds_no_connection.py`).
- **What the process that serves the door needs**: the database, as every API process; the signing
  key; `CHOREGOS_SECRETS_KEKS` (or the key wrapper your edition supplies), with which a call's arguments
  wait at rest until an instance claims them, and without which no call can be placed; and, on
  PostgreSQL, the `LISTEN` connection every replica already holds, which now also carries the
  outposts' wake-ups (channel `choregos_outpost_calls`, ids only). Where that connection is down, a
  poll still finds a call at its next look, 5 to 10 seconds later.
- **One poll per instance.** The slot is on the instance's row and is given back when the poll answers
  or its client goes away. If a replica is killed, the slots of the polls it held lapse within 30
  seconds; until then those instances read 409 `poll_conflict` with `retry_after`, and each raises one
  alert (below). Stop replicas gracefully.

**The alert to wire.** A second poll under a key that already has one waiting is what a stolen key
looks like. The door refuses it and writes the audit event `outpost.poll_conflict` in the
organisation's audit log, once a minute at most for an instance, under the actor kind `outpost`.
`/metrics` counts those events per organisation as `choregos_outpost_poll_conflicts_total{org}`: alert
on its increase, and answer by revoking the instance
(`POST /api/v1/orgs/{org}/outposts/{slug}/instances/{instance_id}/revoke`), which holds from its next
request. With `monitoring.alerts` and outposts on, the chart ships the rule
`ChoregosOutpostPollConflict` (critical, per organisation) in its one rule group, in both monitoring
flavours. A daemon that restarts while the door still holds its former poll raises it too: read the
audit event before you revoke.

**Rotating the signing key also rotates the nonces.** The key that authenticates the door's nonces is
derived from the signing key. After a rotation a daemon's next request is refused once
(401 `stale_nonce`) with a fresh nonce, and it signs again: nothing to do. While replicas with the
former and the new key run side by side, a daemon may be refused a few times in a row.

**The door's own Deployment** (`global.outposts.door.dedicated: true`,
[ADR 0148](adr/0148-the-outpost-door-is-a-third-door-with-its-own-contract.md) §8). Polls wait by the
hundred. Split out, they take no worker and no pooled connection from the console and the runs: the
chart renders a Deployment `choregos-outposts-door` from the API image, started as
`python -m choregos_api.outposts` (`apps/api/src/choregos_api/outposts/alone.py`), a Service of the
same name, a disruption budget, and the route then leads there.

| Value (under `global.outposts.door`) | Default | What it does |
| :-- | :-- | :-- |
| `dedicated` | `false` | false: the API serves the door. true: the Deployment below does, and the route leads to it |
| `replicas` | `2` | its replicas; `podDisruptionBudget.minAvailable` (1) keeps one through a drain |
| `pool.size`, `pool.maxOverflow`, `pool.timeoutSeconds` | `5`, `5`, `10` | this Deployment's own connection pool (`CHOREGOS_DB_POOL_SIZE`, `CHOREGOS_DB_MAX_OVERFLOW`): each replica holds at most `size + maxOverflow` connections. Add `replicas × (size + maxOverflow)` to what you size PostgreSQL's `max_connections` for |
| `terminationGracePeriodSeconds` | `45` | how long a pod has to stop. The process lets the polls it holds end by themselves, 30 seconds at most, then cancels what is left; a value that is not above 30 fails the render |
| `extraEnv` | `[]` | variables for this Deployment only. It does not receive `global.extraEnv`, which carries connector secrets |
| `resources` | 100m / 256Mi, 1 / 512Mi | its requests and limits |

- **What that process serves**: the four verbs, `/healthz` and `/readyz` (the database answers), and
  no other route of the API (`apps/api/tests/test_outpost_door_alone.py`). It listens for the
  outposts' wake-ups on its own `LISTEN` connection, and for nothing else of the platform's events.
- **What it holds**: the database's URL, the door's signing key, and `CHOREGOS_SECRETS_KEKS`. A
  database credential in a pod that faces other parties' machines is new, and it is all it holds: not
  the session secret, the run-token key, the gateway's key, the object store's or the identity
  provider's. It mounts no service account token.
- **The sweep stays in the API.** What ran out of time at an outpost nobody polls any more is closed
  by a loop that lists every outpost under the platform's scope, which the door never takes. The API
  runs it in both layouts, and the door's own process never does: keep the API running.
- **The API still mounts the door**, in both layouts: `CHOREGOS_OUTPOSTS` is one switch. With the door
  split out no route leads to the API's copy from outside the cluster.
- **Network policies** (`networkPolicies.enabled`): the door's pods get a policy of their own,
  `choregos-outposts-door-net`. They take requests from the entrance's namespace
  (`choregos-api.ingress.gatewayNamespace`) and reach DNS and the database, nothing else; they are
  left out of the platform's own egress policy. Nothing in this chart opens a way to an outpost,
  because there is none to open: an outpost dials out.
- **Not there**: the door's own process exposes no `/metrics`. The poll-conflict series is the API's
  (it counts audit events), but the door's request counts are not in `http_requests_total` once it is
  split out.

### The outpost's side: the chart `choregos-outpost`

Whoever runs an outpost installs the daemon in THEIR cluster with a chart of its own, published
beside the platform's (`oci://ghcr.io/vargafoundation/charts/choregos-outpost`). It is not a subchart
of this one and shares no value with it: its page is `charts/choregos-outpost/README.md`, and a
complete example is `charts/choregos-outpost/values/example.yaml`. What you hand its operator is
what the management API returns with an enrolment token: the door's origin (`policy.control_plane`),
the door's keys (`policy.door_keys`), and the token itself. Hand them
[Running an outpost](outpost-operator.md) with it: that page is written for them, and says first
that an outpost does not give data residency.

| Value | Default | What it does |
| :-- | :-- | :-- |
| `image.digest`, `image.tag` | empty | the daemon's image, by digest when one is given; else by tag, the chart's own version |
| `replicas` | `1` | instances of the outpost: a StatefulSet, each replica with a claim of its own for its keys, its journal and its audit log (`persistence.size`, `.storageClass`) |
| `policy.control_plane`, `policy.door_keys` | required | the door's origin and the keys the outpost obeys; without either the render fails and says where they come from |
| `policy.endpoints.<alias>` | `{}` | the policy file's own fields (`url`, `tools`, `writes`, `timeout_s`, `max_payload_kb`), plus `auth.secretName` and `auth.secretKey`: the credential is a Secret the operator creates, mounted as a file whose path the chart writes as `auth.from_file`. A credential typed as a value fails the render |
| `policy.concurrency`, `policy.audit.file` | `4`, `/var/log/choregos-outpost/audit.jsonl` | the ceiling of calls at once, and the local audit log, on the instance's claim |
| `enrolment.existingSecret`, `.key` | `<name>-enrolment`, `token` | the Secret that holds the single-use token. An init container enrols the instance only when it has no identity yet |
| `proxy.url` or `proxy.existingSecret`; `ca.configMap` or `ca.secret` | empty | the outbound proxy towards the door, and an authority to trust besides the system's |
| `graceSeconds`, `terminationGracePeriodSeconds` | `20`, `30` | how long calls in progress have to finish, and the pod's own period, which must be above it |
| `networkPolicy.enabled`, `.controlPlane.cidrs` | `true`, `[]` | egress to DNS, the door and the servers the endpoints name; no ingress at all. Without the door's addresses its port is opened to public addresses only, since a NetworkPolicy cannot name a host |
| `servers` | `[]` | MCP servers the operator chooses to run beside the daemon, each admitted to by the daemon's pods only ([ADR 0149](adr/0149-a-tool-may-live-at-an-outpost.md) §9). The platform deploys none |

The daemon's pod is locked down as a plugin server's is (no service account token, not root, a
read-only root filesystem, no capability), and only its state and its audit log are writable
(`tests/charts/test_outpost_chart.py`). An instance that exits with code 3 (the door no longer
serves its protocol) or 4 (revoked) restarts in a loop on purpose: the chart's notes say so.

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
an administrator installs an entry from *Library → MCP servers* (it then appears under *Administration → Installed connectors*). Two chart values
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

## Where the console's help links point

The console's **Help** entry, in the top bar, and the "Learn more" links of its pages open the user
documentation, `https://docs.varga.foundation/choregos` by default. An installation that cannot reach it
(air-gapped) or serves its own copy sets `CHOREGOS_DOCS_URL` on the API, with the chart's `extraEnv`:

```yaml
global:
  extraEnv:
    - name: CHOREGOS_DOCS_URL
      value: https://docs.intranet.example/choregos
```

The API serves it as `docs_url` in `GET /edition`, without its trailing slash; the console appends the
page's path to it (`/integrate/a2a`, `/cli/install`…). A value that is not an `http(s)` address is
ignored and the public site is used.

## Plugins

A plugin packages agents, skills, templates, evals, procedures and MCP servers in one signed format
([ADR 0093](adr/0093-an-agent-ships-as-a-plugin.md), [writing one](plugins.md)). The deployment says
where plugins come from, which ones every organisation gets, and which ones are baked into the image:

```yaml
global:
  plugins:
    sources:                      # the chart's default: the platform's own agents
      - name: varga
        index: oci://ghcr.io/vargafoundation/choregos-agents/index:latest   # or https://…/index.yaml
        trust:                    # a keyless cosign signer (regular expressions, matched whole)…
          identity: https://github\.com/VargaFoundation/choregos-agents/\.github/workflows/publish\.yml@refs/heads/main
          issuer: https://token\.actions\.githubusercontent\.com
          # key: /etc/choregos/plugins-keys/acme.pub   # …or a public key, mounted with api.extraVolumes
        egress: []                # hosts its fetches may reach beyond the index's own
    defaultPlugins:               # the chart's default: the built-in agents (ADR 0093 §6)
      - { source: varga, name: software-delivery, version: "^1.0", preapproved: true }
      - { source: varga, name: netops, version: "^1.0", preapproved: true }
      - { source: varga, name: authoring, version: "^1.0", preapproved: true }
      - { source: varga, name: mcp-clients, version: "^1.0", preapproved: true }
      - { source: varga, name: requests, version: "^1.0" }
    offline: /opt/choregos/plugins   # plugins baked into the image: air-gapped, demo, CI
```

The chart renders these values into the ConfigMap `choregos-plugins`, mounted read-only in the API at
`/etc/choregos/plugins/plugins.yaml` and named by `CHOREGOS_PLUGINS_CONFIG`. By default they install the
platform's own agents — `software-delivery`, `netops`, `authoring`, `mcp-clients` and `requests` of
[`VargaFoundation/choregos-agents`](https://github.com/VargaFoundation/choregos-agents) — from the image's
offline directory, where the API image ships them pinned by `plugins.lock`: no network is needed at
start-up. `defaultPlugins: []` installs none (the agent catalogue still offers them, one by one);
`sources: []`, `defaultPlugins: []` and `offline: ""` render nothing at all. A source without `trust`
refuses to render; there is no unsigned source.

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
- a default plugin the gate sends to a second administrator (one that brings an MCP server, or a
  high-risk skill) is **not** installed by the platform: it waits for an administrator's approval. It is
  logged (`plugin.default.pending_approval`) and listed by `GET /api/v1/orgs/{org}/plugin-approvals`
  with `default: true`; any administrator of the organisation approves it there;
- **unless the entry says `preapproved: true`**: listing it so is your approval, given through the
  review of this deployment's values. The plugin is installed and the audit trail records
  `plugin.default.preapproved` with the gate's reasons and `approved_by: deployment configuration`. The
  API offers no such flag. The default `software-delivery` needs it: its `madr-4` skill links to
  adr.github.io and is rated high risk;
- a default of a declared source is taken from the offline directory when it holds a version inside the
  requested range (`from_offline` in `plugin.default.installed`), and recorded under its source, which
  serves later upgrades.

**Upgrading from 0.20.** The built-in agents left the core: the migration `b8d0f2a4c6e1` rewrites
their provenance (`catalogue:<slug>@<v>` → `plugin:<plugin>/<slug>@<v>`) and records an `org_plugins`
row (source `varga`) for each plugin an organisation held agents of, so upgrades keep reaching them; an
agent the organisation changed is still offered updates, never overwritten. A deployment that keeps
`defaultPlugins: []` and no `varga` source still lists the agents in the catalogue; upgrading those rows
then needs the `varga` source declared.

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

Alembic runs as a Helm hook before the API starts, through `python -m choregos_api.migrate`
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

**Tool scripts: the organisation decides, the deployment sets the default and keeps a brake.** Each organisation switches tool scripts for its runs ([ADR 0122](adr/0122-an-agent-may-call-its-run-s-tools-from-a-script.md) §6, amended 2026-11-05, S57-05b): its administrator sets `tool_scripts` to `enabled` or `disabled` with `PATCH /orgs/{org}` (or on *Administration → Tool scripts*), or to `null` to follow the deployment. Two orchestrator values, read by every worker:

| Value | Env | Default | Meaning |
|:--|:--|:--|:--|
| `choregos-orchestrator.runner.toolScripts.enabled` | `CHOREGOS_TOOL_SCRIPTS` | `false` | The **default** of an organisation that does not choose. `false`: its runs are offered no tool bridge; `true`: its ceiling and each project's policy decide, and a policy without `tools` offers nothing. An organisation's `enabled` or `disabled` wins over it. |
| `choregos-orchestrator.runner.toolScripts.allowed` | `CHOREGOS_TOOL_SCRIPTS_ALLOWED` | `true` | The **hard brake**. `false`: no run of any organisation is offered tool scripts, whatever its setting says. |

Before S57-05b, `enabled: false` refused tool scripts to every run; it now only sets the default, and an organisation administrator can enable them for their organisation. To keep them refused everywhere — while the runner image predates the bridge (S57-07: an older runner refuses a stage input that carries `tools.scripts`), or in an emergency — set `allowed: false`. The API does not know these values: the console says "the deployment's default" without saying which.

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
PKCE (S256) with a signed, single-use `state` and a `nonce`. Register a confidential client with the
redirect URI `<api_url>/api/v1/auth/callback` and the scopes `openid profile email groups`.

Discovery is **required**, and the document's `issuer` must equal `global.oidc.issuer`. When it cannot be
read, or names another issuer, the sign-in answers 503 and tries again on the next attempt (ADR 0100);
there is no guess at a Keycloak layout any more. A provider that publishes no discovery document is
configured with its three endpoints instead: `choregos-api.oidcEndpoints.authorization`, `.token` and
`.userinfo` (all three or none; the render fails otherwise). A per-organisation provider (ADR 0108) follows
the same rules with its own `issuer` and endpoints.

The userinfo answer must give `sub` and `email`. A sign-in is bound to `sub`: a returning subject keeps its
account even when its e-mail changed, and an existing account is linked by e-mail only while it is not bound
to another subject. A provider that says `email_verified: false` never links an existing account by e-mail
(`choregos-api.oidcRequireEmailVerified`, default `true`; a provider that omits the claim, such as Entra ID,
is accepted); it also refuses a new account for such an address and keeps a returning account's stored
address. An account bound to another subject is refused, except that a per-organisation provider re-links
an address of its own domains (audited `user.relink`), so an organisation can move to its own provider.
When you **migrate the instance's identity provider** (every subject changes), set
`choregos-api.oidcRelinkVerifiedEmail: true` so accounts whose address the new provider asserts as
`email_verified: true` are re-linked, then set it back to `false`. When the token endpoint returns an ID token, its `iss`, `aud` and `nonce` must match the
sign-in and its `sub` must equal userinfo's; otherwise the sign-in is refused (401).

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

**Checking the sign-in configuration from the console.** *Admin → Identity* shows a read-only card,
"How people sign in to this instance", for the platform administrator: the OIDC issuer, client id and
discovery URL, whether the client secret is set (never its value), the redirect URI to register at the
identity provider (`<apiUrl>/api/v1/auth/callback`), the MCP door's OAuth issuer, audience and registered
clients, the session lifetime, the development login switch, and the bootstrap organisation and the
number of bootstrap administrators (never their e-mails). It is the answer of
`GET /api/v1/platform/identity`. Nothing is editable there: the values come from the chart
(`global.oidc`, `global.mcp.oauth`), and a row marked `default` was not set by the deployment. When
`oidc.issuer` reads "not configured", the instance still runs on the built-in development issuer.

**An organisation with its own identity provider** ([ADR 0108](adr/0108-each-organisation-signs-in-with-its-own-identity-provider.md)).
The settings above are the instance's provider. A tenant that brings its own (OIDC or SAML) is configured
in the edition that declares a sign-in resolver, not in the chart; the card's row "Organisations' own
identity providers" says whether one is declared. Its members sign in from the console's sign-in page by
typing their work address (its domain picks the provider) or through the link `/login?org=<slug>`. The
tenant registers the same redirect URI, `<apiUrl>/api/v1/auth/callback`. A tenant's provider signs in only
addresses on the domains its tenant owns, and grants roles on that tenant only.

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

The same callback measures what fills an ACP CLI's context (ADR 0141 §4): for a run of `claude-code`,
`codex`, `gemini-cli`, `goose`, `opencode` or `copilot-cli`, it splits each call's request into the
parts the runner registered (paragraph digests, never the text) and `backend`, scales them to the
call's `prompt_tokens`, and posts at most one breakdown per 5 seconds per run to the API's internal
routes, each signed for its run with the gateway's master key. The embedded LiteLLM gets
`CHOREGOS_CONTEXT_API_URL` (`global.internalApiUrl` + `/api/v1/internal`) and the NetworkPolicy
`choregos-litellm-to-api`. An external LiteLLM measures nothing unless it loads the callback and sets
`CHOREGOS_CONTEXT_API_URL` and `LITELLM_MASTER_KEY` (the same key as the API's
`CHOREGOS_GATEWAY_MASTER_KEY`) itself; an ACP CLI's run then says "not measurable" until its first
breakdown.

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
only ours. The runner pods the chart does not render carry it too: the `agent_sandbox` template, the
`choregos-runner` ServiceAccount and every `k8s_job` runner pod, so a job step's own image pulls
from the same registry (S51-09). The secret must already exist in the namespace; on a shared platform it is usually the
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
`tests/charts/test_image_per_component.py` renders both.

### Demonstration fakes (`demoFakes`)

`demoFakes.enabled: true` deploys **one** pod — the API image, `python -m
choregos_adapters.fakes.server` — serving the fakes of the HR scenario: MCP endpoints for the device
manager, the carrier, the badge readers and the supplier's agent
(`http://choregos-demo-fakes:8090/{mdm,shipping,access_control,supplier}/mcp`), and a fake
Microsoft Graph (`…/graph/v1.0`, `…/login`). One replica is the point: the API runs two and the
orchestrator is apart, and in-memory fakes would show each process its own directory. The network
policy opens that pod to the platform; `demoFakes.tokenSecret` (a secret reference) protects it
further. A demonstration: the chart refuses it in `staging` and `prod`. `trials/hr/` plays the HR
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
| `edition_licence` | the licence the edition's extension announces through `declare(..., licence=...)`; `null` on the community edition |
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
| API start-up | `CHOREGOS_EDITION=enterprise` while no extension declared itself | the image is the community one, or `choregos-ee` is not installed, or its `choregos.plugins` entry point does not call `declare`. The refusal names all three |

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
the sink URL, `…/api/v1/webhooks/tekton?token=<secret>` (the former spelling was removed in 0.24.0,
see "Upgrading to 0.24"). On any deployed instance (`staging` or `prod`, or any instance served over https whatever its
`CHOREGOS_ENV`), an unsigned GitHub or Tekton webhook, whose secret is not configured, is refused with a 401;
only a local http instance accepts it.

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

## Upgrading past 0.27.1

Changes merged after v0.27.1, shipped with the next release.

**The GitHub App can be created from the console** (S70-21, [ADR 0161](adr/0161-the-github-app-is-created-from-a-manifest.md)). A platform administrator registers it on GitHub from a manifest; its credentials are sealed in the new table `github_app_registrations` (created empty by the migration). Nothing to do for a deployment whose settings name the App: they still win. To use it, keep `secrets-keks` configured ([the GitHub App](#the-github-app)).

## Upgrading to 0.27

One permission changes. The API now deletes the objects of a task artifact it erases or purges (an assistant's
task output, S63-24), where 0.26 left them to the bucket's lifecycle rule.

| What | Why | What to do |
| :-- | :-- | :-- |
| `s3:DeleteObject` on the object store's bucket and prefix | The erasure of a person's or a subject's data (S63-16) and the retention purge delete the bytes before the rows. A store that refuses the delete fails the erasure (502, nothing erased) and keeps the expired rows for the next purge pass. | Add the permission to the credentials of `objectStore.secretRef` (an IAM policy, or the MinIO/RustFS policy of the key). A lifecycle rule on the bucket is no longer needed for this, and stays harmless. No migration. |

## Upgrading to 0.26

0.26.0 brings assistants (stream S63, ADRs 0133-0140): a plugin may ship one, an organisation installs it
for an audience, and people converse with it in the console, through the MCP door or in an embedded
frame. Nothing changes for an organisation that installs no assistant. The migrations run with the
chart's job as usual; what an operator does:

| What | Why | What to do |
| :-- | :-- | :-- |
| `CHOREGOS_TOKEN_EXCHANGE_AUDIENCES` | The audiences a person's token may be exchanged for (`user_exchange`, ADR 0135). Unset, a connector's editor chooses any audience the identity provider allows. | Set it in `global.extraEnv` to a JSON list of your sources' audiences before binding a need under `user_exchange` ([security](security.md), "Review of the person's identity"). |
| The identity provider | `user_exchange` needs token exchange (RFC 8693) enabled for the API's client and limited to the sources' audiences; a standing delegation (ADR 0137) needs `offline_access`, bounded and revocable offline sessions. | Configure the realm; nothing changes for deployments without assistants. |
| Open sessions of `/responses` | The migration that makes sessions durable closes the sessions open at upgrade time (`reason: migrated`, their spend booked). | Upgrade outside working hours if hosted prompt agents are in use. |
| Connected accounts | Accounts are now bound to the authorization server the person consented to (`provider_digest`). | Accounts made before 0.26 (none outside a test deployment) are connected again once. |
| Conversation suites (ADR 0140) | An installed assistant's conversation suites gate the promotion of its agent version: the platform plays them through the gateway and a judge model scores their rubrics (`conversation_judge_model`, else the `strong` model profile). | With no gateway key, a version whose assistant ships suites is refused promotion with the reason; `conversation_suites_require_model=false` opts out (no check then). |
| The embedded component | A host page frames `/embed/assistants/{org}/{installation}` only from the installation's `config.embed_origins`. | List the host origins in the installation's configuration; nothing else to deploy. |

The standing delegation (ADR 0137) is shipped but its ADR stays `proposed` until the works council's
opinion and the data protection impact assessment of the deploying organisation: do not offer delegated
tasks to people before then.

## Upgrading to 0.24

0.24.0 removes every external French alias whose window closed with 0.23.0 (S45-49,
[ADR 0098](adr/0098-english-everywhere-finished.md) 'Windows'). Each one warned, or was documented as
deprecated, from 0.21 to 0.23. Nothing is aliased any more: a former name below fails (an
`ImportError`, an `AttributeError` or a `TypeError` for a Python name, a refused catalogue, a 401 for
the Tekton sink) or has no effect (an environment variable, a label, a tool key). Switch to the
replacement before upgrading. Kept on purpose, and not listed: Temporal patch ids, the French activity
and signal wire names, the readers of persisted rows and histories (ADR 0098 §4), the entry-point
group `choregos.plugins`, the Grafana dashboard uids and the French ticket fixture
`py-rounding-bug-fr`. <!-- lang: fr-ok -->

### Operators: deployment, chart and contract

| Removed | Replacement | What to do |
| :-- | :-- | :-- |
| `CHOREGOS_ESSAI_ONTOLOGIE=1` | `CHOREGOS_ONTOLOGY_TRIAL=1` | Rename it in `global.extraEnv` (or `.env`): the former variable no longer activates the ontology extension. <!-- lang: fr-ok --> |
| `CONNECTOR_DEMO_JETON` | `CONNECTOR_DEMO_TOKEN` | Rename it: the former variable is no longer read, and a demo fakes process with only it set asks for no token. <!-- lang: fr-ok --> |
| `python -m choregos_api.migrer` | `python -m choregos_api.migrate` | Change a custom migration command; the chart already runs the new one. <!-- lang: fr-ok --> |
| `choregos-admin = choregos_api.outils:main`, `choregos_api.outils` | `choregos_api.admin_cli:main`, `choregos_api.admin_cli` | The `choregos-admin` script already points at the new module. <!-- lang: fr-ok --> |
| `python -m choregos_core.catalogue_d_agents.evals` | `python -m choregos_core.agent_catalogue.evals` | Change a scheduled eval job. <!-- lang: fr-ok --> |
| `python -m choregos_adapters.fakes.serveur` | `python -m choregos_adapters.fakes.server` | Change your own container command of the demo fakes; the chart already runs the new one. <!-- lang: fr-ok --> |
| `outils:` (top-level key of the tool catalogue) | `tools:` | Rename the key: a catalogue with the former key is refused at load. <!-- lang: fr-ok --> |
| `…/api/v1/webhooks/tekton?jeton=<secret>` | `…/api/v1/webhooks/tekton?token=<secret>` | Change the Tekton CloudEvents sink URL: the former parameter is no longer read, so the sink gets a 401. <!-- lang: fr-ok --> |
| The `legacy_alertname` label on `ChoregosApi5xxErrors`, `ChoregosRunPending`, `ChoregosDailyBudgetHigh` and `ChoregosTrainFrozen` | `alertname` with the English name | Rewrite an Alertmanager route, inhibition or silence that matched `legacy_alertname`. |
| `docs/runbooks/montee-temporal.md` (redirect stub) | `docs/runbooks/temporal-upgrade.md` | Nothing for the chart's alerts, whose `runbook_url` is English; fix a bookmark or a hand-written annotation. <!-- lang: fr-ok --> |
| `docs/runbooks/perte-de-noeud.md` (redirect stub) | `docs/runbooks/node-loss.md` | Same. <!-- lang: fr-ok --> |
| `docs/runbooks/restauration-postgres.md` (redirect stub) | `docs/runbooks/postgres-restore.md` | Same. <!-- lang: fr-ok --> |
| `docs/runbooks/run-bloque.md` (redirect stub) | `docs/runbooks/stuck-run.md` | Same. <!-- lang: fr-ok --> |
| `docs/runbooks/train-gele.md` (redirect stub) | `docs/runbooks/train-frozen.md` | Same. <!-- lang: fr-ok --> |
| `docs/runbooks/webhook-perdu.md` (redirect stub) | `docs/runbooks/lost-webhook.md` | Same. <!-- lang: fr-ok --> |
| `docs/runbooks/rotation-secrets.md` (redirect stub) | `docs/runbooks/secret-rotation.md` | Same. <!-- lang: fr-ok --> |
| `GET /runs/{id}/access`: `evenements`, `deduites`, `acces` (with `acces[].motifs` and the nature `autre`) | `events`, `inferred`, `access` (with `access[].reasons` and the nature `other`) | Read the English fields; the former ones are no longer served nor in the contract. <!-- lang: fr-ok --> |
| `deprecated_code` on a workflow issue and on a problem's `errors[]` (`role.playbook_introuvable`) | `code` (`role.playbook_not_found`) | Match on `code`; the field is no longer served nor in the contract. <!-- lang: fr-ok --> |
| A stored eval matrix or schedule naming a fixture by its French name (`py-bug-arrondi`, `py-remise-volume`, `py-test-manquant`, `py-hors-perimetre`, `py-refactor-sans-regression`, `node-slug-longueur`) | `py-rounding-bug`, `py-volume-discount`, `py-missing-test`, `py-out-of-scope`, `py-refactor-without-regression`, `node-slug-length` | Rename it: a former name plays no ticket any more (`FORMER_FIXTURE_NAMES` is gone). <!-- lang: fr-ok --> |
| The workflow editor's draft kept by a tab under `choregos.brouillon.<project>.<workflow>` | `choregos.draft.<project>.<workflow>` | Nothing: a draft left in a tab's storage by a console older than 0.21 is no longer resumed. <!-- lang: fr-ok --> |

### Extension authors: the extension and edition API

Every name below raised a `DeprecationWarning` naming its replacement until 0.23.0; it is now gone.
The choregos-ee coordination is [#1229](https://github.com/VargaFoundation/choregos/issues/1229).

| Removed | Replacement |
| :-- | :-- |
| module `choregos_api.greffons` | `choregos_api.extensions` <!-- lang: fr-ok --> |
| `declarer_un_routeur`, `routeurs_declares`, `reinitialiser` | `declare_router`, `declared_routers`, `reset` <!-- lang: fr-ok --> |
| `declarer_une_section_d_administration`, `sections_declarees`, `chemins_d_une_section` | `declare_admin_section`, `declared_sections`, `section_paths` <!-- lang: fr-ok --> |
| `FournisseurDOutils`, `declarer_un_fournisseur_d_outils`, `fournisseurs_d_outils` | `ToolProvider`, `declare_tool_provider`, `tool_providers` <!-- lang: fr-ok --> |
| `FournisseurHumain`, `declarer_des_outils_pour_les_humains`, `fournisseurs_humains` | `HumanToolProvider`, `declare_human_tools`, `human_tool_providers` <!-- lang: fr-ok --> |
| `declarer_un_installateur_de_gabarit`, `installateurs_de_gabarit` | `declare_template_installer`, `template_installers` <!-- lang: fr-ok --> |
| `GESTES`, `DemandeDeGeste` (and its `.geste`, `.cible`), `GesteRefuse` (and its `.raison`) | `GESTURES`, `GestureRequest` (`.gesture`, `.target`), `GestureRefused` (`str(refusal)`) <!-- lang: fr-ok --> |
| `GestureRefused` natures `interdit`, `conflit` | `forbidden`, `conflict` (a former one is a `ValueError`) <!-- lang: fr-ok --> |
| `declarer_un_controle_de_geste`, `controler`, `reinitialiser_les_controles` | `declare_gesture_check`, `check_gesture`, `reset_gesture_checks` <!-- lang: fr-ok --> |
| `_ROUTEURS`, `_FOURNISSEURS_D_OUTILS`, `_FOURNISSEURS_HUMAINS`, `_INSTALLATEURS`, `_CONTROLES`, `_televersements` | `_ROUTERS`, `_TOOL_PROVIDERS`, `_HUMAN_TOOL_PROVIDERS`, `_INSTALLERS`, `_GESTURE_CHECKS`, `_uploads` <!-- lang: fr-ok --> |
| keywords `lister=`, `appeler=`, `en_attente=` (and the attributes of the same names) of the tool providers | `list_tools=`, `call_tool=`, `pending=` <!-- lang: fr-ok --> |
| `ecriture: true` in a tool announced by a human tool provider | `write: true` (the former key has no effect: the tool is a read) <!-- lang: fr-ok --> |
| module `choregos_api.effets` | `choregos_api.effects` <!-- lang: fr-ok --> |
| `EffetRefuse`, `ContexteEffet`, `Executer` | `EffectRefused`, `EffectContext`, `Execute` <!-- lang: fr-ok --> |
| `declarer_un_effet(..., politique=)`, `politique_de_l_effet`, `effet`, `effets_declares`, `rendre`, `reinitialiser` | `declare_effect(..., policy=)`, `effect_policy`, `effect`, `declared_effects`, `render`, `reset` <!-- lang: fr-ok --> |
| `choregos_core.quotas`: `DEFAUT`, `Fournisseur`, `declarer_un_quota`, `quota_pour`, `reinitialiser`, `Quota(memoire=)`, `Quota.memoire` | `DEFAULT`, `QuotaProvider`, `declare_quota`, `quota_for`, `reset`, `Quota(memory=)`, `Quota.memory` <!-- lang: fr-ok --> |
| `choregos_core.admission`: `DemandeDeRun`, `AdmissionRefusee`, `Controle`, `declarer_une_admission`, `admissions_declarees`, `verifier`, `reinitialiser` | `RunRequest`, `AdmissionRefused`, `AdmissionCheck`, `declare_admission`, `declared_admissions`, `check`, `reset` <!-- lang: fr-ok --> |
| `choregos_core.secrets`: `SecretIntrouvable`, `declarer_un_resolveur` | `SecretNotFound`, `declare_resolver` <!-- lang: fr-ok --> |
| `choregos_api.edition`: `COMMUNAUTAIRE`, `ENTREPRISE`, `declarer(..., fonctions=)`, `declarer_le_mappeur_de_groupes`, `mappeur_de_groupes`, `courante`, `fonctions`, `est_entreprise` | `COMMUNITY`, `ENTERPRISE`, `declare(..., features=)`, `declare_group_mapper`, `group_mapper`, `current`, `features`, `is_enterprise` <!-- lang: fr-ok --> |
| `choregos_api.edition`: `SessionRefusee`, `declarer_une_validation_de_session`, `validations_de_session`, `declarer_un_administrateur_de_plateforme`, `administrateurs_de_plateforme`, `reinitialiser` | `SessionRefused`, `declare_session_validation`, `session_validations`, `declare_platform_administrator`, `platform_administrators`, `reset` <!-- lang: fr-ok --> |
| `Principal.authentifie_le` | `Principal.authenticated_at` <!-- lang: fr-ok --> |
| module `choregos_api.migrer` (with `emplacements_des_greffons`, `GROUPE_DE_MIGRATIONS`), `configuration(avec_les_greffons=)` | `choregos_api.migrate` (`extension_locations`, `MIGRATIONS_GROUP`), `configuration(with_extensions=)` <!-- lang: fr-ok --> |
| package `choregos_core.catalogue_d_agents` (and `.evals`): `EntreeDuCatalogue`, `DOSSIER`, `entrees`, `entree`, `skills_du_catalogue`, `fichiers_de_la_skill` | `choregos_core.agent_catalogue` (and `.evals`): `CatalogueEntry`, `FOLDER`, `entries`, `entry`, `catalogue_skills`, `skill_files` <!-- lang: fr-ok --> |
| module `choregos_adapters.fakes.serveur` | `choregos_adapters.fakes.server` <!-- lang: fr-ok --> |
| module `choregos_contracts.issue_codes` (`RENAMED_ISSUE_CODES`, `canonical_issue_code`, `deprecated_issue_code`) | none: issue codes are English only <!-- lang: fr-ok --> |
| `choregos_core.compat.EXTERNAL_ALIAS_REMOVAL` | none: pass the release to `alias_names(..., removed_in=)` |

## Upgrading to 0.21

Read this page top to bottom before upgrading from 0.20.x: each item below can stop a start, fail a
render or change what runs, and says how to see it and what to do. The identifier renames of the
chart are not repeated here: they are in [Renamed in 0.21 (ADR 0098)](#renamed-in-021-adr-0098).

### Session secret (S47-01)
The API refuses to start when `CHOREGOS_SESSION_SECRET` is empty. On a deployed instance (staging or
prod, or a public or API URL over https) it also refuses a secret shorter than 32 bytes or the
published default. You see it in the API pod's log at start-up. Generate one with
`openssl rand -base64 48` before upgrading. Only the API checks it; the workers and the Jobs do not.
`CHOREGOS_SESSION_SECRET_PREVIOUS` (rotation) in `global.extraEnv` now fails the render: use the
`session-secret-previous` key of the API secret, or `externalSecrets.sessionSecretPrevious`. Sign-ins
in flight at the upgrade (10 minutes at most) fail once; session cookies of the previous release
keep working for one release.

### Trusted proxies (S47-02)
The sign-in and webhook rate limit keys on the address the nearest trusted proxy saw, not on the
client-written `X-Forwarded-For`. `choregos-api.trustedProxyCidrs` defaults to the private ranges.
If a load balancer, CDN or WAF with public addresses sits in front of the API, add its ranges, or
every client behind it shares one window. On-prem, narrow the list to your proxies. The limit
applies per replica.

### CORS origins (S47-03)
The chart renders `CHOREGOS_CORS_ORIGINS`, and the API refuses cross-origin cookie mutations. A
`CHOREGOS_CORS_ORIGINS` in `global.extraEnv` fails the render: move it to `choregos-api.corsOrigins`
(a list). Left empty, it defaults to the console's public URL.

### OIDC discovery and subject binding (S47-04, ADR 0100)
OIDC discovery is mandatory and its `issuer` must equal `CHOREGOS_OIDC_ISSUER`; the Keycloak
fallback is gone and a failed discovery answers 503, retried at the next sign-in. A provider
without discovery needs `choregos-api.oidcEndpoints` (all three endpoints). A console sign-in is
bound to the provider's `sub`, and an address the provider reports as `email_verified: false` never
links, renames or creates an account (`choregos-api.oidcRequireEmailVerified`, default true). To
migrate the instance's provider, set `choregos-api.oidcRelinkVerifiedEmail: true` temporarily, then
back to `false`. Accounts created before this change are not migrated.

### Roles from the identity provider (S47-05)
An IdP sign-in now removes the organisation roles it granted (`memberships.source` `idp`) and no longer maps; roles
granted by administrators stay. Roles that the provider withdrew before the upgrade are not
revoked retroactively: review them once from the members page.

### Governance the platform does not enforce (S47-11, ADR 0101)
A policy that sets `approvals.*`, `attempts.default_max` or `review.require_human_for_risk` is
refused with 422 when saved or when a project is created from a template; a workflow with
`review.humans` or review agents other than the `by` actor is invalid. Stored documents keep
loading. The `advanced` template is version 3 and no longer promises a maintainer review. Remove
the keys from the policies you write.

### Catalogue tools need `read_only` (S47-12)
A POST or MCP catalogue tool without `read_only: true` is no longer listed or callable. Mark pure
readers `read_only: true`; move writers to an organisation MCP connector.

### Webhooks and tracker commands (S47-13, S47-14, S47-38, ADR 0102)
- Unsigned GitHub and Tekton webhooks are refused on any https instance, even with
  `CHOREGOS_ENV=dev`: set `CHOREGOS_GITHUB_WEBHOOK_SECRET` and `CHOREGOS_GENERIC_WEBHOOK_SECRET`.
- A `/choregos ...` comment on a tracker ticket no longer reaches the workflow: Choregos replies with
  a console link and audits it. A webhook for an ended interpreter no longer fails.
- `/choregos pause|resume|stop` in a signed GitHub delivery applies with the rights of the member
  whose GitHub numeric user id an organisation administrator linked. Decisions still need the console.
- Moving a card on a GitHub Projects or Jira board no longer moves the work item: move it from the
  console. The board follows Choregos, not the reverse.

### Project allowlist of backends (S47-20)
`agent.allowed_backends` is now enforced at stage start. A stage on a backend the project does not
list escalates with "the agent backend `x` is not one this project allows". Check existing projects
before upgrading: a project seeded with `['opencode']` whose workflow names a claude-code actor is
refused.

### MCP gateway (S46-09, S46-12, ADR 0105)
Nothing changes unless `global.mcpGateway` is set (an organisation's own gateway, never part of this
chart). When it is:
- `global.mcpGateway.internalHosts` is rendered as `CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS`: the exact
  host names the platform treats as the gateway. The API refuses them as a run's destination and
  lets the platform itself call them.
- `namespace`, `podLabels` (in-cluster) or `cidrs` (external) and `ports` open egress for the `api`
  and `workers` families, and `hosts` are appended to those families of `egress.ceiling`, when
  declared. Runners never get them, and the opening is revoked when the organisation moves or
  deletes the gateway.
- An organisation whose gateway is in `required` mode can no longer declare or call direct `mcp`
  connectors. Organisation connectors that are plugin servers (`org_connectors.plugin_server` set)
  are exempt: they run in the cluster, run by the platform, and are not upstreams. `mcp-gateway`
  connectors are called through the gateway with the signed identity of the call.

### Changes merged after 0.20.6
- **Runner confinement** (ADR 0094): the runner confines the agent with Landlock by default.
  `CHOREGOS_RUNNER_CONFINEMENT` in the chart's `runner.env` is `off`, `best_effort` (default) or
  `enforce`; `enforce` on a kernel without Landlock ends the run with exit code 90. The run's
  journal records `agent.confinement`. See [usage](usage.md).
- **Plugins** (ADR 0093): `global.plugins.sources`, `defaultPlugins` and `offline` are rendered into
  the ConfigMap `choregos-plugins` (`CHOREGOS_PLUGINS_CONFIG`); an unusable configuration stops the
  API at start-up. See [Plugins](#plugins).
- **Plugin servers** (ADR 0096): `global.plugins.servers.enabled`, `namespace` and `registries` give
  the API a Role in the `choregos-plugins` namespace, under the Kyverno policy
  `infra/policies/plugin-servers.yaml`. See [Plugin servers](#plugin-servers).

## Operating it

What the chart does for a multi-node installation, and the knobs behind it:

| Concern | Mechanism | Value |
| :-- | :-- | :-- |
| A node drain must not take the API, the front or a worker queue down | one `PodDisruptionBudget` per component, and per orchestrator queue — rendered only when there is more than one replica (a budget on a single replica blocks the drain and protects nothing) | `choregos-api.podDisruptionBudget`, `choregos-web.podDisruptionBudget`, `choregos-orchestrator.podDisruptionBudget` |
| Two replicas must not share a node | pod anti-affinity on `kubernetes.io/hostname`: `soft` (preferred, the default — a one-node bench still schedules), `hard` (one replica per node, or no pod), `none` | `global.antiAffinity` |
| The database must not be exhausted under load | a **bounded** connection pool per process: at most `size + maxOverflow` connections, a short wait beyond. Size PostgreSQL's `max_connections` as (API replicas + workers) × (size + maxOverflow), plus room for migrations and an operator | `global.database.pool.{size,maxOverflow,timeoutSeconds}` |
| A serious environment must not run on bench dependencies | the chart **refuses to render** in `staging` and `prod` when any `embedded` dependency, `devSecrets`, `devLogin` or `demoFakes` is on, and names the value | `templates/guard.yaml` |
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
`global.egress.enabled` with an empty value (`templates/guard.yaml`): a proxy nobody sends traffic to
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

### Rate limits on sign-in and webhooks

The routes without a principal are rate-limited: sign-in (`/api/v1/auth/*`) and every webhook under
`/api/v1/webhooks/` (GitHub, Tekton, Argo CD, Alertmanager, Jira, GitLab, and the plugin tracker route
`POST /api/v1/webhooks/plugins/{org}/{plugin}/{server}`, whose deliveries come from outside providers
through the ingress). Past `CHOREGOS_RATE_LIMIT_PER_MINUTE` requests (600 by default; 0 switches it
off) in a sliding minute from one client, the API answers `429` with `Retry-After`. Authenticated routes
are bounded by their principal, not here.

**Per replica.** Each API replica counts in its own memory: with N replicas a client gets up to
N × `CHOREGOS_RATE_LIMIT_PER_MINUTE` (the chart runs 2 to 8). There is no shared store, by decision:
the stack has no Redis, and the limit is there to stop one address scanning sign-in or flooding a
webhook, not a distributed attack, which belongs to the ingress or a WAF. Each replica holds at most
10 000 client keys and evicts the least recently seen beyond that.

**Keyed on the address the nearest trusted proxy saw.** The key is never the left-most
`X-Forwarded-For` entry, which the client writes: rotating that header buys no new window. A client
outside the trusted networks cannot spend someone else's budget by forging their address (a provider's
webhook address, say); a client inside them can (see below). An IPv6 client is keyed by its /64.

| Setting | Chart value | Default | What it does |
| :-- | :-- | :-- | :-- |
| `CHOREGOS_TRUSTED_PROXY_CIDRS` | `choregos-api.trustedProxyCidrs` | code: `[]`; chart: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7` | a JSON list of the proxies' networks. A peer outside them is the client; otherwise `X-Forwarded-For` is read right to left and the first entry outside them is the client. Empty: the TCP peer is the client and the header is ignored |
| `CHOREGOS_TRUSTED_PROXY_HOPS` | `choregos-api.trustedProxyHops` | `0` | only without CIDRs: the number of proxies that each append one entry; the client is the entry the farthest of them appended |

The API refuses to start on a value of `CHOREGOS_TRUSTED_PROXY_CIDRS` that is not a network, and when
both settings are set. The chart owns both names: `global.extraEnv` naming either fails at render.

The chart trusts the private ranges because every in-cluster hop (the gateway, the console's Next
server) has a private address. The cost is that any other client inside a trusted range that can reach
the API can write `X-Forwarded-For` entries the API will believe:

- **Runner pods, on any cluster with a private pod network, cloud or on-prem.** They run agent code,
  their pod address is inside the default ranges, and `choregos-runner-egress` lets them call the API
  directly. Agent code can send webhook requests that carry a provider's address (GitHub's, say) and
  spend that provider's budget on every replica the Service reaches. The provider's real deliveries then get `429` until the minute
  passes, and GitHub does not redeliver on its own. An invalid signature does not help: the limiter
  counts before the route checks it.
- **On-prem clients** with private addresses, in the same way.

**Narrow the list** to the networks of the ingress controller (or gateway) pods and the console pods
where the pod network allows it, i.e. where those pods draw from an IP pool that runners do not use (a
dedicated node pool's pod range, a CNI IP pool per namespace). With every hop's address known, list
only those (`--set-json 'choregos-api.trustedProxyCidrs=["10.42.0.0/16"]'`). On a flat pod network,
where every pod shares one pod CIDR, the default leaves the runner case open. #1061 tracks trusting
proxies by identity rather than by range.

**Widen the list** when a load balancer, CDN or WAF in front of the API has public proxy addresses: add
every address it connects from or writes into `X-Forwarded-For` to `choregos-api.trustedProxyCidrs`
(Google Cloud load balancers: `35.191.0.0/16`, `130.211.0.0/22` and the forwarding rule's own address,
which they append after the client's; a CDN: its edge ranges). `--set-json` replaces the whole list, so
keep the private ranges in it. Otherwise the API takes that proxy's address for the client, and every
client behind it shares one window.

The console's Next server is a trusted hop: it proxies `/api/v1` to the API, so the browser's address
must reach the API in `X-Forwarded-For`. **If every console sign-in shares one window** (one user's
retries throttle everyone), the web hop is not forwarding `X-Forwarded-For`, and the key has become the
web pod's address.

### The run's API and the run door

`/api/v1/internal/*` is the run's own API: a runner reads its input and posts its events and its result
there, a hosted session's pod takes its turns, the per-run egress proxy reads a run's network, the
outbound relay reads an organisation's proxy. Each presents a token of its own. The API's route sends
the whole `/api` prefix of the public hostnames to the API, so these routes answered there as well.
Runs do not come that way: they call `global.internalApiUrl`, which is the in-cluster Service unless
you changed it. The API therefore guards these routes by the name a request arrives under
([ADR 0144](adr/0144-the-run-s-api-answers-only-where-runs-are.md)).

| Setting | Chart value | Default | What it does |
| :-- | :-- | :-- | :-- |
| `CHOREGOS_INTERNAL_API_GUARD` | `choregos-api.internalApiGuard` | `warn` | `off`: nothing. `warn`: a request for the run's API that arrives under a public hostname is served and logged (`internal_api.would_refuse`). `enforce`: it gets the 404 of an unknown route, before any token is read (`internal_api.refused`) |
| `CHOREGOS_PUBLIC_HOSTS` | `choregos-api.ingress.hostnames` when the chart renders the route, plus `choregos-api.publicHostnames` | code: `[]`; chart: the route's hostnames | public hostnames beyond the hosts of `global.publicUrl`, `global.apiUrl` and `choregos-api.corsOrigins`, which always count. Put in `publicHostnames` a name the chart cannot know: a route made outside it, a second name on a load balancer. `*.` may lead |
| `CHOREGOS_RUN_DOOR_HOSTS` | `choregos-api.runDoor.hostnames` | `[]` | run doors: hostnames the run's API answers under although they are public. The chart renders their route, `choregos-run-door` |
| — | `choregos-api.runDoor.annotations` | `{}` | the door Ingress's own annotations (`ingress.kind: ingress`) |
| `CHOREGOS_INTERNAL_API_URL` | `global.internalApiUrl` | `http://choregos-api:8000` | where runs call back: the workers hand it to each run, the per-run egress proxy uses it, and the API reads it so that the guard never refuses that address |

A request *arrives under* the name in its `Host` header and under every name in `X-Forwarded-Host`
(a reverse proxy that rewrites `Host`, as the console's relay of `/api/v1` does, writes where the
request came in there). One public name among them is enough to refuse. A name in `X-Forwarded-Host`
never opens anything: writing a run door there does not open a public hostname. Ports and case are
not compared. Everything else is served as before: the Service's name in any of its forms, a compose
service, `localhost`, an address.

The API refuses to start on a mode outside the three, and on a door or a public hostname written as a
URL or with a port. The chart owns the first three names: `global.extraEnv` naming one fails the
render, and so does a door hostname that is also in `choregos-api.ingress.hostnames`.

**What `enforce` cannot do: refuse the address runs are sent to.** When the host of
`global.internalApiUrl` is itself a public hostname and no run door covers it, refusing there would end
every run at its first call. The guard then keeps warning whatever the mode, and the API says so once
at start-up:

```json
{"event": "internal_api.guard_not_enforced", "level": "warning", "mode": "enforce", "callback_host": "api.example.com", "detail": "runs call back on `api.example.com`, a public hostname of this API that is not a run door: …"}
```

Without `CHOREGOS_INTERNAL_API_URL` (an installation that is not the chart's nor the compose file's),
the API assumes runs call its own public URL, which holds `enforce` back in the same way.

**Runs outside the cluster need a run door.** A job of the `aca` executor cannot reach the Service:
such an installation has a public hostname in `global.internalApiUrl` today. Give those runs a
hostname of their own before enforcing:

1. Choose a hostname that is not one of the API's (`runs.example.com`), point it at the ingress and
   serve it over TLS: the run token travels on it.
2. List it in `choregos-api.runDoor.hostnames`. The chart renders `choregos-run-door`, an Ingress or an
   HTTPRoute like the API's (`ingress.kind`, `className`, `gateway`), which sends `/api/v1/internal`
   to the API and nothing else: the console, the REST API, the doors and the webhooks do not answer on
   that hostname.
3. Restrict it where your ingress can: the source addresses your runs leave from, a request rate.
   With `ingress.kind: ingress` the door's `annotations` are its own, so a rule written there does not
   land on the public route; with the Gateway API, attach your implementation's policy to the route
   `choregos-run-door`.
4. Set `global.internalApiUrl` to `https://runs.example.com`. It is one value for every run of the
   installation: stages that run in the cluster call the door too, so check that their pods can reach
   it (with `networkPolicies.enabled`, `choregos-runner-egress` lets runner pods reach the API pods,
   not an ingress).
5. Then `enforce`, in a later roll-out, once the runs started before step 4 have ended: a run keeps
   the address it was started with, and those still call the public hostname.

```yaml
global:
  internalApiUrl: https://runs.example.com
choregos-api:
  internalApiGuard: enforce
  ingress:
    kind: ingress
    className: nginx
  runDoor:
    hostnames: ["runs.example.com"]
    annotations:
      # ingress-nginx's names; another controller has its own.
      nginx.ingress.kubernetes.io/whitelist-source-range: "203.0.113.0/24"
      nginx.ingress.kubernetes.io/limit-rps: "50"
```

**Upgrading.** The default is `warn`, and no preset of the chart enforces: an upgrade refuses nothing.

1. Deploy the release as it is and let runs go through it, hosted sessions and the outbound relay
   included if you use them.
2. Read the API's log for `internal_api.guard_not_enforced` (once per pod at start-up) and
   `internal_api.would_refuse`. Each line of the second carries `host`, `header` (`host` or
   `x-forwarded-host`) and `path_prefix`: `/api/v1/internal/runs` is a run, `…/agent-sessions` a hosted
   session's pod, `…/orgs` the outbound relay.
3. A line that is one of your own callers names something to move first: runs outside the cluster to a
   run door (above), a relay or a script to the Service or to the door. Lines that are nobody of yours
   are what `enforce` will stop answering. After moving a caller, wait until its lines stop: a run in
   flight keeps the address it was started with.
4. Set `choregos-api.internalApiGuard: enforce`, staging first. `internal_api.refused` then counts what
   is refused. To go back, set `warn`: nothing else changes.

An installation that decides to keep its runs on a public hostname sets `off`: `warn` logs one line
per call back.

A run that ends at its first call after the switch is in
[the stuck-run runbook](runbooks/stuck-run.md#a-run-that-ends-at-its-first-call).

**What it is, and what it is not.** It closes the run's API on the hostnames the deployment declared,
for requests that come in through them. It does not stop a caller inside the cluster, which uses the
Service's name: the tokens and `networkPolicies` do. A hostname routed to the API that nobody declared
stays served until it is in `publicHostnames`, and a proxy in front of the API that rewrites `Host`
without writing `X-Forwarded-Host` hides the public name from the guard. A run door is as public as
its route.

### The CLI served by the instance (S62-08)

The API image carries the native `choregos` binaries (S62-07) in `/opt/choregos/cli/`, with their
`SHA256SUMS` and `manifest.json` (`CHOREGOS_CLI_DIR` points elsewhere). The instance serves them on the
console's host, so a person installs the CLI of the exact version it runs without reaching GitHub:

| Path | What it answers |
| :-- | :-- |
| `/downloads/cli/manifest.json` | the manifest: `version`, and per file its `name`, `os`, `arch`, `sha256`, `size` |
| `/downloads/cli/<name>` | a file the manifest lists, or `SHA256SUMS`: `Content-Length`, a strong `ETag` (`"sha256-<hex>"`), `Range` honoured. Any other name is a 404: names are looked up in the manifest, never joined to a path |
| `/cli/install.sh` | the Linux and macOS install script (`curl -fsSL <instance>/cli/install.sh \| sh`) |
| `/cli/install.ps1` | the Windows install script (`irm <instance>/cli/install.ps1 \| iex`) |

The scripts carry `CHOREGOS_PUBLIC_URL` (refused with a 503 when it is not a plain `http(s)` URL), read
the manifest and the binary from that host, and check the sha256 before installing. A build without a
bundle answers `404` with `code: no_cli_bundled` and the GitHub release of the running version
(`release_url`); a manifest that does not parse, or a file whose size is not the manifest's, answers
`503`, never a doubtful file. The chart routes `/downloads` (prefix) and the two scripts (exact paths)
to the API, beside `/api` and `/mcp`.

**Public, on purpose**, like `/healthz` and `/edition`: the CLI is open source and carries no secret, the
scripts must work with plain `curl` and `irm` before the person holds a token, and the manifest says
only the version `/edition` already says. An instance behind a single sign-on proxy must let these four
paths through it, as it does the probes. What needs guarding is the bandwidth: these routes have a rate
limit of their own, `CHOREGOS_DOWNLOAD_RATE_LIMIT_PER_MINUTE` (60 requests per minute and per address by
default, per replica, keyed as below; 0 switches it off), separate from the sign-in budget.

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

## The decision model (Laya behind LiteLLM)

A workflow's `decision` step (ADR 0121, decided in PR #1183)
asks typed questions of a decision model through the gateway's `POST /v1/systemone` route. The chart
ships the first one, **Laya** (`convaiinnovations/laya-multilingual`, mmBERT-base, 322M parameters,
100+ languages, weights under Apache-2.0), on CPU, off by default:

```yaml
global:
  gateway:
    embedded: true                  # required: only the embedded LiteLLM serves it
  decisions:
    embedded: true                  # deploys `charts/choregos/charts/laya` and adds `platform/decide` to LiteLLM
    apiKeySecret: ""                # key `api-key`; empty: the chart generates one and keeps it
    inputCostPerToken: 0.00000002   # USD per token read: the internal price (ADR 0004)
```

What it renders:

- **`<release>-laya`**, a Deployment of `ghcr.io/vargafoundation/choregos-laya` (`docker/laya.Dockerfile`):
  `laya-serve` with the checkpoint baked in at a pinned commit, `HF_HUB_OFFLINE=1`, nothing fetched at
  run time. It asks for 2 GiB of memory (measured on 2026-11-03: 1.6 GiB resident with the checkpoint loaded, 1.95 GiB after a reload) and opens its port
  only once the checkpoint is loaded; the probes read `GET /health`, which answers without a credential.
- **A NetworkPolicy** that admits LiteLLM's pods only, on port 8000, and lets Laya reach nothing. The
  platform never calls a decision model directly: the cost is counted and the cap enforced at the
  gateway. With `networkPolicies.enabled`, `choregos-allow-platform` opens the platform's egress to it.
- **`platform/decide`** in LiteLLM's `model_list`:

  ```yaml
  - model_name: platform/decide
    litellm_params:
      model: typesafe/multilingual          # Laya speaks TypeSafe Jev's wire
      api_base: http://<release>-laya:8000  # LiteLLM posts to <api_base>/v1/systemone
      api_key: os.environ/LAYA_API_KEY      # from the Secret above, sent as a bearer
    model_info:
      mode: evaluation
      input_cost_per_token: 0.000000020000
      output_cost_per_token: 0.000000000000
  ```

  LiteLLM 1.105.5 lists `laya/*` in its price map but has no `laya` provider in its code: its
  documentation's `laya/` deployments need a later build. Laya serves the `/v1/systemone` wire
  exactly, so the `typesafe` provider pointed at Laya's Service carries it; `tests/live/test_decisions_live.py`
  proves it against LiteLLM 1.105.5 (answers, virtual key, model scope, the spend log at the internal price).

Laya counts the tokens it read as `input_tokens` and answers none, so its spend is
`input_tokens × inputCostPerToken`. Its probabilities are over-confident as shipped (Laya's own
documentation): until the decision ledger has measured it on a workflow, a threshold on its confidence
is a guess.

Each model of `choregos-litellm.models` may also name its own `apiBase`, `apiKeyEnv` (the environment
variable LiteLLM reads its key from) and `inputCostPerToken` / `outputCostPerToken`; an entry that sets
none of them renders as before.

On a laptop, `COMPOSE_PROFILES=decisions docker compose up` (or the line in `.env`) starts Laya and
routes `platform/decide` to it, in fakes mode as with a model key. Set the profile this way rather than
with `--profile decisions`: the variable is also what tells `litellm-config` to add the alias.

## Renamed in 0.21 (ADR 0098)

0.21 renames the French operational identifiers of the chart, the Kyverno policies and the runbooks
([ADR 0098](adr/0098-english-everywhere-finished.md)). A rename of a name that something outside the
chart reads (an alert route, a PolicyException, a mounted path) keeps a shim for one release or tells
you what to do by hand; this section lists each. The shims went in 0.24.0: the tables below are
the record of the 0.21 renames, and where a row says a former name "still works" or "warns", that
held until 0.23.0. "Upgrading to 0.24" lists what was removed.

**Internal aliases and shims are gone (S45-40).** Where a Python row below says "internal" and
"alias keeps working" or "a shim keeps it importable", that held while another package still
imported the former name. Every user has switched, and the aliases and shims defined under
`packages/` are deleted: `choregos_core.acces`, `choregos_core.sorties`,
`choregos_core.dsl.exigences`, `choregos_adapters.fakes.rh`, `choregos_adapters.fakes.cd_de_demo`,
`choregos_adapters.familles`, `choregos_adapters.memory.lexicale`,
`choregos_adapters.tracker.champs`, `choregos_adapters.tracker.interne` and
`choregos_runner.outils_locaux`, and every former name of those packages' `alias:internal` lines.
The names marked `alias:external` resolved until 0.23.0 and were removed in 0.24.0 (S45-49).

**Internal aliases and shims under `apps/` are gone too (S45-46).** The same holds for the rows
below whose former name is marked internal or "a shim": every importer has switched, and the shims
defined under `apps/api` and `apps/orchestrator` are deleted: `choregos_api.adaptateurs`,
`amorcage`, `fraicheur`, `limiteur`, `metriques`, `mcp.appelant`, `mcp.garde_fous`, `mcp.outils`,
`schemas.identite`, `livraison`, `mesures`, `plateforme`, `projets`, `services.catalogue_d_agents`,
`connecteurs`, `courtier`, `couts`, `evenements`, `gabarits`, `memoire`, `parcours`, `projets`,
`recus`, `routage`, `choregos_orchestrator.repartition`, `workflows.planification`,
`activities.bilan` and `activities.garde`, with every former name of the `alias:internal` lines of
those apps and the former keywords of the wrappers (`proposer`, `publier_workflow`,
`installer_skills_et_agents`, `Caller(jeton=...)`, `RateLimiter(par_minute=...)`,
`workers_by_queue(activites=...)`). The test-only variable `CHOREGOS_ARCHIVER_HISTORIQUE` is no longer
read. The `alias:external` windows (`greffons`, `effets`, `edition`, `migrer`, `outils`,
`Principal.authentifie_le`) are unchanged.
<!-- lang: fr-ok -->

### Alerts

The rule expressions, thresholds and `for:` durations are unchanged. Each renamed alert carries its
former name in the `legacy_alertname` label, so a route, an inhibition or a silence written against
the old `alertname` keeps matching if you add `legacy_alertname` to its matchers.

| Former alert | New alert | What an operator does |
| :-- | :-- | :-- |
| `ChoregosApiErreurs5xx` | `ChoregosApi5xxErrors` | Route on `alertname` = the new name, or on `legacy_alertname` = the former one until 0.23.0. <!-- lang: fr-ok --> |
| `ChoregosRunEnAttente` | `ChoregosRunPending` | Same. <!-- lang: fr-ok --> |
| `ChoregosBudgetJournalier` | `ChoregosDailyBudgetHigh` | Same. <!-- lang: fr-ok --> |
| `ChoregosTrainGele` | `ChoregosTrainFrozen` | Same. <!-- lang: fr-ok --> |

`ChoregosBacklogTemporal` keeps its name. A silence is matched on `alertname`, so a silence created
for a former name no longer silences the new alert: recreate it, or match `legacy_alertname`. In
Alertmanager:

```yaml
route:
  routes:
    - matchers: ['alertname=~"ChoregosApi5xxErrors|ChoregosRunPending|ChoregosDailyBudgetHigh|ChoregosTrainFrozen"']
      receiver: platform-oncall
    # Until 0.23.0, the rule that still names the former alerts keeps working with:
    - matchers: ['legacy_alertname="ChoregosTrainGele"']
      receiver: platform-oncall
```

`legacy_alertname` is removed in 0.23.0.

The rule group is renamed too (the rules inside it are the same objects):

| Former rule group | New rule group | What an operator does |
| :-- | :-- | :-- |
| `choregos.plateforme` | `choregos.platform` | A dashboard or a recording rule that filters on `rule_group` must follow. <!-- lang: fr-ok --> |

### Runbooks

The six runbooks below, and the secret rotation one, moved. The former path stays as a one-line
redirect stub until 0.23.0: the `runbook_url` annotation of an alert already deployed points at it.
The chart's alerts now point at the new paths.

| Former runbook | New runbook |
| :-- | :-- |
| `docs/runbooks/montee-temporal.md` | `docs/runbooks/temporal-upgrade.md` <!-- lang: fr-ok --> |
| `docs/runbooks/perte-de-noeud.md` | `docs/runbooks/node-loss.md` <!-- lang: fr-ok --> |
| `docs/runbooks/restauration-postgres.md` | `docs/runbooks/postgres-restore.md` <!-- lang: fr-ok --> |
| `docs/runbooks/run-bloque.md` | `docs/runbooks/stuck-run.md` <!-- lang: fr-ok --> |
| `docs/runbooks/train-gele.md` | `docs/runbooks/train-frozen.md` <!-- lang: fr-ok --> |
| `docs/runbooks/webhook-perdu.md` | `docs/runbooks/lost-webhook.md` <!-- lang: fr-ok --> |
| `docs/runbooks/rotation-secrets.md` | `docs/runbooks/secret-rotation.md` <!-- lang: fr-ok --> |

### Kyverno policies

Kyverno has no alias: a rule is known by its name, and a PolicyException or a PolicyReport query that
names the former one stops matching silently. **Before upgrading**, update the exceptions and the
queries to the new names (an exception for an old name is not rejected; it just no longer excepts,
and the workload is then refused). The `choregos-plugins` namespace matches `choregos-*`, so an
exception you wrote for a plugin server pod under `pas-de-hostpath` must move to `no-hostpath` too.

| Former | New | What an operator does |
| :-- | :-- | :-- |
| Rule `interdire-latest` of `choregos-image-tags` | `forbid-latest-tag` | Update the PolicyExceptions (`spec.exceptions[].ruleNames`) and PolicyReport queries. <!-- lang: fr-ok --> |
| Rule `verifier-signature` of `choregos-verify-images` | `verify-signature` | Same. <!-- lang: fr-ok --> |
| Rule `pas-de-hostpath` of `choregos-pod-security` | `no-hostpath` | Same, including exceptions for the `choregos-plugins` namespace. <!-- lang: fr-ok --> |
| Rule `labels-obligatoires` of `choregos-pod-security` | `required-labels` | Same. <!-- lang: fr-ok --> |
| Policy `choregos-quotas-et-runtime` | `choregos-quotas-and-runtime` | Update the PolicyExceptions (`spec.exceptions[].policyName`) and delete the former policy if you applied it by hand (an Argo CD sync prunes it). <!-- lang: fr-ok --> |

The messages of the policies are English now; a script that matched the French text of a refusal
must match the rule name instead.

### Chart

| Former | New | What an operator does |
| :-- | :-- | :-- |
| Template `templates/garde.yaml` (the render-time guard) | `templates/guard.yaml` | Nothing for an install; a patch or a Kustomize overlay that names the file must follow. <!-- lang: fr-ok --> |
| Template `templates/secrets-embarques.yaml` (`devSecrets`) | `templates/embedded-secrets.yaml` | Nothing for an install; the Secret it renders keeps its name. <!-- lang: fr-ok --> |
| Init container `attendre-la-base` of the migration Job | `wait-for-database` | A Kyverno or Gatekeeper rule, or a log query, that names the init container must follow. <!-- lang: fr-ok --> |
| `global.toolCatalog.mountPath` default `/etc/choregos/outils` | `/etc/choregos/tools` | The API reads the catalogue from the path the chart sets, so nothing breaks; a sidecar or a debug command that reads the old path must follow. To keep the old path, pin `global.toolCatalog.mountPath: /etc/choregos/outils`. <!-- lang: fr-ok --> |
| Dashboard ConfigMaps `choregos-dashboard-couts`, `choregos-dashboard-memoire`, `choregos-dashboard-plateforme` | `choregos-dashboard-costs`, `choregos-dashboard-memory`, `choregos-dashboard-platform` | The Grafana uids are unchanged (`choregos-couts`, `choregos-memoire`, `choregos-plateforme`), so links, folders and alert annotations keep working; the sidecar reloads them. <!-- lang: fr-ok --> |

### Variables and `python -m` modules

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `CHOREGOS_ESSAI_ONTOLOGIE=1` | `CHOREGOS_ONTOLOGY_TRIAL=1` | Rename it in `global.extraEnv`; the former one still works until 0.23.0 and warns. <!-- lang: fr-ok --> |
| `CONNECTOR_DEMO_JETON` | `CONNECTOR_DEMO_TOKEN` | The demo fakes' chart switches itself; rename it in your own values or `.env`; the former one is still read when the new one is unset (until 0.23.0). <!-- lang: fr-ok --> |
| `python -m choregos_adapters.fakes.serveur` | `python -m choregos_adapters.fakes.server` | The chart switches the demo fakes' command; change your own container command; the former one still serves until 0.23.0. <!-- lang: fr-ok --> |
| `python -m choregos_api.migrer` | `python -m choregos_api.migrate` | Change a custom migration command; the former one still works until 0.23.0. <!-- lang: fr-ok --> |
| `python -m choregos_core.catalogue_d_agents.evals` | `python -m choregos_core.agent_catalogue.evals` | Same. <!-- lang: fr-ok --> |

### Operator-facing names

Variables, commands, catalogue keys, files and the trial scripts a person runs or edits by hand.

#### Entry points and variables (S45-05)

| Former | New |
| :-- | :-- |
| `python -m choregos_api.migrer` | `python -m choregos_api.migrate` <!-- lang: fr-ok --> |
| `choregos-admin = choregos_api.outils:main` | `choregos-admin = choregos_api.admin_cli:main` (`choregos_api.outils.app` and `.main` are the same objects) <!-- lang: fr-ok --> |
| `CHOREGOS_ESSAI_ONTOLOGIE=1` (logs a deprecation warning when it is the only one set) | `CHOREGOS_ONTOLOGY_TRIAL=1` <!-- lang: fr-ok --> |
| `CHOREGOS_TEST_SUITE_SUR_POSTGRES` (CI sets both; the conftest switches in S45-42) | `CHOREGOS_TEST_SUITE_ON_POSTGRES` <!-- lang: fr-ok --> |
| `python -m choregos_core.catalogue_d_agents.evals` | `python -m choregos_core.agent_catalogue.evals` <!-- lang: fr-ok --> |

#### S45-08: the rest of the core in English

<!-- lang: fr-ok -->

#### The tool catalogue key: `outils:` becomes `tools:` (S45-08)

The YAML catalogue of tools (`CHOREGOS_CATALOGUE_PATH`, `demo/tools/catalogue.yaml`) is read
under the new key `tools:`. The former key `outils:` keeps loading until 0.23.0 and logs one
deprecation warning per process. A catalogue that declares both keys is refused. A bare YAML list
still maps to the tools.

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `outils:` (top-level key of the catalogue) | `tools:` | Rename the key before 0.23.0. Nothing else in a tool entry changes. |

#### S45-32: `essai/` becomes `trials/`, in English

The trials moved and were translated; no trial was re-run and no result changed. Nothing is kept
under the former names: they were scripts, compose files and pages, not an API. A person who ran a
trial by hand uses the new paths, environment variables and names below.

#### Folders and files (S45-32)

| Former | New |
| :-- | :-- |
| `essai/` | `trials/` |
| `tests/essai/` | `tests/trials/` |
| `essai/demarrage-local/` | `trials/local-start/` |
| `essai/integration-claude/` | `trials/claude-integration/` |
| `essai/investissement/` | `trials/investment/` |
| `essai/ontologie/` | `trials/ontology/` |
| `essai/plateforme-dev/` | `trials/dev-platform/` |
| `essai/projet-dev/` | `trials/dev-project/` |
| `essai/rh/` | `trials/hr/` |
| `essai/sans-secret/` | `trials/no-secret/` |
| `RESULTATS.md` | `RESULTS.md` |
| `decor.py` | `scenery.py` |
| `preparer_la_demo.py` | `prepare_the_demo.py` |
| `portee.py` | `scope.py` |
| `essai.py` (lakehouse) | `trial.py` |
| `scenario_rh.py` | `scenario_hr.py` |
| `analyse_infra.md` | `infra_analysis.md` (the playbook of the role `infra_analysis`) |
| `greffon.Dockerfile` | `extension.Dockerfile` |
| `01-role-applicatif.sql` | `01-application-role.sql` |
| `.etat.json` (dev-platform, git-ignored) | `.state.json` |

`demo`, `knowledge`, `lakehouse`, `python` and `graphify` keep their names. The recorded data files
(`graphify/*.json`, `graphify/questions.sha256`, `no-secret/runtime/Cargo.lock`,
`investment/sources-2026-10-14.json`) keep their bytes.

#### Script commands (S45-32)

| Former | New |
| :-- | :-- |
| `scenario_rh.py preparer / arrivee [--dans N] / suivre / deplacer / depart / verifier` | `scenario_hr.py prepare / arrival [--in N] / follow / move / departure / verify` |
| `scenario_dev.py preparer / exigences / ouvrir / suivre / verifier` (dev-project) | `scenario_dev.py prepare / requirements / open / follow / verify` |
| `scenario_dev.py preparer / verifier` (dev-platform) | `scenario_dev.py prepare / verify` |

#### Environment variables (S45-32)

| Former | New |
| :-- | :-- |
| `ESSAI_FAUX_URL` | `TRIAL_FAKES_URL` |
| `ESSAI_JETON_REF` | `TRIAL_TOKEN_REF` |
| `ESSAI_BACKEND` | `TRIAL_BACKEND` |
| `ESSAI_MODELE` / `ESSAI_MODELES` | `TRIAL_MODEL` / `TRIAL_MODELS` |
| `ESSAI_RH_PROJET` | `TRIAL_HR_PROJECT` |
| `ESSAI_DEMO_ORG` | `TRIAL_DEMO_ORG` |
| `ESSAI_INSTALLATION` | `TRIAL_INSTALLATION` |
| `ESSAI_DEPOT` | `TRIAL_REPOSITORY` |
| `ESSAI_PROJET` | `TRIAL_PROJECT` (the default value `essai-it4it` is kept: it is the slug of a project that exists on the dev tenant) |
| `ESSAI_PORT_API` | `TRIAL_API_PORT` |
| `GARDER=1` (local-start `run.sh`) | `KEEP=1` |
| `CHOREGOS_ESSAI_ONTOLOGIE=1` in `trials/local-start/compose.yaml` | `CHOREGOS_ONTOLOGY_TRIAL=1` (see S45-05) |

#### Names inside the trials (S45-32)

| Former | New |
| :-- | :-- |
| Kubernetes namespaces, images and secret `essai-control`, `essai-run`, `essai-api`, `essai-orchestrator`, `essai-internal` (`trials/no-secret`) | `trial-control`, `trial-run`, `trial-api`, `trial-orchestrator`, `trial-internal` |
| kind cluster `essai-sans-secret`, variable `ESSAI_INTERNAL_TOKEN` | `trial-no-secret`, `TRIAL_INTERNAL_TOKEN` |
| Compose projects `essai-socle`, `essai-lakehouse`; images `choregos-api:essai`, `choregos-orchestrator:essai` | `trial-foundation`, `trial-lakehouse`; `choregos-api:trial`, `choregos-orchestrator:trial` |
| Development credentials `essai-dev-only` and the S3 key `essai` | `trial-dev-only` and `trial` |
| Agent role `analyse_infra` and the workflow actors and transitions of `workflow-it4it.yaml` (`analyste`, `exploitant`, `t-instruire`, `t-clore`, outputs `proposition` and `constat`) | `infra_analysis`, `analyst`, `operator`, `t-investigate`, `t-close`, outputs `proposal` and `finding` |
| `python -m choregos_api.migrer` in the local-start compose file | `python -m choregos_api.migrate` |

The Rust crate keeps its name `choregos-runtime-essai`, because `Cargo.lock` is recorded data.

#### S45-43: the history-recording switch has an English name

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `CHOREGOS_ARCHIVER_HISTORIQUE` | `CHOREGOS_RECORD_HISTORY` | Nothing for CI. A developer re-recording histories sets the English name: the former one is no longer read (S45-46). <!-- lang: fr-ok --> |

The orchestrator test helpers that other test modules imported moved to `apps/orchestrator/tests/kits/`
(`_jusqu_a` -> `wait_until_state`, `_demandes` -> `requests_of` / `requests_of_item`,
`_worker_avec` -> `worker_with`, `DEPOT` -> `REPOSITORY`, and so on). They are test-internal: nothing
to do. <!-- lang: fr-ok -->

### Extension and edition API (external, until 0.23.0)

Names an extension package or an edition imports. The former ones keep working, warn with a `DeprecationWarning` that names the replacement, and go in 0.23.0.

#### S45-03: French keys read under both names

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `ecriture: true` in a tool an extension announces to the MCP door (`declarer_des_outils_pour_les_humains`) | `write: true` | Nothing today. An extension may declare `write`; `ecriture` is still read (`alias:external`, ADR 0098 §3 (f)). <!-- lang: fr-ok --> |
| `TrainInput.seuil()` | `TrainInput.threshold()` | Nothing: an internal name, an alias keeps working. <!-- lang: fr-ok --> |

The payload keys, error types and persisted values listed in ADR 0098 §4 (`effets`, `jusqu_a`,
`cle`, `refus`, `AgentIndisponible`, `ontology.effet`, `params.ontologie`, the audit key
`ecriture`, `TrainInput.seuil_de_relais` and the others) are not renamed: since S45-03 every reader
also accepts the English spelling, nothing writes it yet (S45-39), and no row or history is
rewritten. <!-- lang: fr-ok -->

#### S45-04: the extension API in English, French names warned until 0.23.0

An **extension** is a package loaded through the `choregos.plugins` entry point (a "plugin" is the
ADR 0093 package). Every French name of the extension API below keeps working until 0.23.0 and
raises a `DeprecationWarning` naming its English replacement. The two former modules
`choregos_api.greffons` and `choregos_api.effets` are the same module objects as
`choregos_api.extensions` and `choregos_api.effects`, so a patch on one is a patch on the other.
<!-- lang: fr-ok -->

#### `choregos_api.extensions` (was `choregos_api.greffons`) (S45-04)

| Former | New |
| :-- | :-- |
| `declarer_un_routeur` | `declare_router` <!-- lang: fr-ok --> |
| `routeurs_declares` | `declared_routers` <!-- lang: fr-ok --> |
| `reinitialiser` | `reset` <!-- lang: fr-ok --> |
| `declarer_une_section_d_administration` | `declare_admin_section` <!-- lang: fr-ok --> |
| `sections_declarees` | `declared_sections` <!-- lang: fr-ok --> |
| `chemins_d_une_section` | `section_paths` <!-- lang: fr-ok --> |
| `FournisseurDOutils(lister, appeler)` | `ToolProvider(list_tools, call_tool)` <!-- lang: fr-ok --> |
| `declarer_un_fournisseur_d_outils(nom, lister, appeler)` | `declare_tool_provider(name, list_tools, call_tool)` <!-- lang: fr-ok --> |
| `fournisseurs_d_outils` | `tool_providers` <!-- lang: fr-ok --> |
| `FournisseurHumain(lister, appeler, en_attente)` | `HumanToolProvider(list_tools, call_tool, pending)` <!-- lang: fr-ok --> |
| `declarer_des_outils_pour_les_humains(nom, lister, appeler, en_attente)` | `declare_human_tools(name, list_tools, call_tool, pending)` <!-- lang: fr-ok --> |
| `fournisseurs_humains` | `human_tool_providers` <!-- lang: fr-ok --> |
| keywords `lister=`, `appeler=`, `en_attente=` (warn) | `list_tools=`, `call_tool=`, `pending=` <!-- lang: fr-ok --> |
| `.lister`, `.appeler`, `.en_attente` of a provider | `.list_tools`, `.call_tool`, `.pending` <!-- lang: fr-ok --> |
| `declarer_un_installateur_de_gabarit` | `declare_template_installer` <!-- lang: fr-ok --> |
| `installateurs_de_gabarit` | `template_installers` <!-- lang: fr-ok --> |
| `GESTES` | `GESTURES` <!-- lang: fr-ok --> |
| `DemandeDeGeste(geste, org, principal, cible)` | `GestureRequest(gesture, org, principal, target)` <!-- lang: fr-ok --> |
| `.geste`, `.cible` of a request | `.gesture`, `.target` <!-- lang: fr-ok --> |
| `GesteRefuse(raison, nature)` | `GestureRefused(reason, nature)` <!-- lang: fr-ok --> |
| nature `interdit`, `conflit` (warn; `.nature` always reads the English one) | `forbidden`, `conflict` <!-- lang: fr-ok --> |
| `declarer_un_controle_de_geste` | `declare_gesture_check` <!-- lang: fr-ok --> |
| `controler` | `check_gesture` <!-- lang: fr-ok --> |
| `reinitialiser_les_controles` | `reset_gesture_checks` <!-- lang: fr-ok --> |
| `_ROUTEURS`, `_FOURNISSEURS_D_OUTILS`, `_FOURNISSEURS_HUMAINS`, `_INSTALLATEURS`, `_CONTROLES`, `_televersements` (private, no warning) | `_ROUTERS`, `_TOOL_PROVIDERS`, `_HUMAN_TOOL_PROVIDERS`, `_INSTALLERS`, `_GESTURE_CHECKS`, `_uploads` <!-- lang: fr-ok --> |

Unchanged: `signatures`, `SECTION_FEATURES` (its three values `block_permission`, `asset_upload`,
`section_icon`), `declare_theme_provider`, `theme_provider`.

#### `choregos_api.effects` (was `choregos_api.effets`) (S45-04)

| Former | New |
| :-- | :-- |
| `EffetRefuse` | `EffectRefused` <!-- lang: fr-ok --> |
| `ContexteEffet` | `EffectContext` <!-- lang: fr-ok --> |
| `Executer` | `Execute` <!-- lang: fr-ok --> |
| `declarer_un_effet(nom, executer, politique=)` (`politique=` warns) | `declare_effect(name, execute, policy=)` <!-- lang: fr-ok --> |
| `politique_de_l_effet` | `effect_policy` <!-- lang: fr-ok --> |
| `effet` | `effect` <!-- lang: fr-ok --> |
| `effets_declares` | `declared_effects` <!-- lang: fr-ok --> |
| `rendre` | `render` <!-- lang: fr-ok --> |
| `reinitialiser` | `reset` <!-- lang: fr-ok --> |
| `_EFFETS`, `_POLITIQUES`, `_appel_de_connecteur`, `_appeler_le_connecteur`, `_arguments_de_la_plateforme` (private, no warning) | `_EFFECTS`, `_POLICIES`, `_connector_call`, `_call_the_connector`, `_platform_arguments` <!-- lang: fr-ok --> |

The DSL value `verifier` (and `motif`, `oui`) of the `check` effect is a recorded value, not renamed.
The Temporal error type string `EffetRefuse` is not renamed either.
<!-- lang: fr-ok -->

#### What an operator does (S45-04)

Nothing today: no deployment setting, row or workflow history changes. A package that imports these
names (choregos-ee) switches to the English ones before 0.23.0, which removes the former names.

#### `choregos_api.edition` (external, removed in 0.23.0) (S45-12)

An extension that calls the edition API reads the new names; the former ones warn.

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `COMMUNAUTAIRE`, `ENTREPRISE` | `COMMUNITY`, `ENTERPRISE` | Rename them in your extension before 0.23.0. <!-- lang: fr-ok --> |
| `declarer(nom, *, fonctions=...)` | `declare(name, *, features=...)` (`publisher=`, `version=`, `licence=` unchanged; `fonctions=` still works and warns) | Rename the call and the keyword before 0.23.0. <!-- lang: fr-ok --> |
| `declarer_le_mappeur_de_groupes`, `mappeur_de_groupes` | `declare_group_mapper`, `group_mapper` | Rename before 0.23.0. <!-- lang: fr-ok --> |
| `courante`, `fonctions`, `est_entreprise` | `current`, `features`, `is_enterprise` | Rename before 0.23.0. <!-- lang: fr-ok --> |
| `SessionRefusee`, `declarer_une_validation_de_session`, `validations_de_session` | `SessionRefused`, `declare_session_validation`, `session_validations` | Rename before 0.23.0. <!-- lang: fr-ok --> |
| `declarer_un_administrateur_de_plateforme`, `administrateurs_de_plateforme` | `declare_platform_administrator`, `platform_administrators` | Rename before 0.23.0. <!-- lang: fr-ok --> |
| `reinitialiser` | `reset` | Rename before 0.23.0. <!-- lang: fr-ok --> |
| `Principal.authentifie_le` (read and write) | `Principal.authenticated_at` | Read the new field before 0.23.0; the former one is a property with a getter and a setter. <!-- lang: fr-ok --> |

`Principal(authentifie_le=...)` as a constructor keyword is not kept: no code in the repository builds
a `Principal` that way, and an extension builds one from a session, not from a literal.
<!-- lang: fr-ok -->

### Python modules and names (aliases)

Internal names. A former module path is a shim that is the same module object as the new one; a former name is an alias. An operator has nothing to change unless a row says so.

#### `choregos_core` (S45-04)

| Former | New |
| :-- | :-- |
| `quotas.Quota(cpu, memoire, pods)`, `.memoire` (the keyword warns) | `Quota(cpu, memory, pods)`, `.memory` <!-- lang: fr-ok --> |
| `quotas.DEFAUT`, `Fournisseur`, `declarer_un_quota`, `quota_pour`, `reinitialiser` | `DEFAULT`, `QuotaProvider`, `declare_quota`, `quota_for`, `reset` <!-- lang: fr-ok --> |
| `admission.DemandeDeRun`, `AdmissionRefusee`, `Controle` | `RunRequest`, `AdmissionRefused`, `AdmissionCheck` <!-- lang: fr-ok --> |
| `admission.declarer_une_admission`, `admissions_declarees`, `verifier`, `reinitialiser` | `declare_admission`, `declared_admissions`, `check`, `reset` <!-- lang: fr-ok --> |
| `secrets.SecretIntrouvable`, `declarer_un_resolveur` | `SecretNotFound`, `declare_resolver` <!-- lang: fr-ok --> |

#### S45-05: English entry points, ontology activation variable and agent catalogue, French names kept until 0.23.0

Every former name below keeps working until 0.23.0. The former modules `choregos_api.migrer`,
`choregos_api.outils` and `choregos_core.catalogue_d_agents` are shims; the former package is the
same module object as `choregos_core.agent_catalogue`, so a patch on one is a patch on the other.
<!-- lang: fr-ok -->

#### `choregos_api.migrate` (was `choregos_api.migrer`) (S45-05)

| Former | New |
| :-- | :-- |
| `emplacements_des_greffons` | `extension_locations` <!-- lang: fr-ok --> |
| `GROUPE_DE_MIGRATIONS` | `MIGRATIONS_GROUP` <!-- lang: fr-ok --> |
| `configuration(avec_les_greffons=...)` (wins when given) | `configuration(with_extensions=...)` <!-- lang: fr-ok --> |

`choregos-admin orgs bootstrap` now prints `organisations created: ...; admins: ...` (`none` when
nothing was created) instead of the French sentence; scripts that matched `aucune` match `none`.

#### `choregos_core.agent_catalogue` (was `choregos_core.catalogue_d_agents`) (S45-05)

| Former | New |
| :-- | :-- |
| `DOSSIER` | `FOLDER` <!-- lang: fr-ok --> |
| `EntreeDuCatalogue(genre, resume, portee, ...)` | `CatalogueEntry(kind, summary, reach, ...)` <!-- lang: fr-ok --> |
| `.genre`, `.resume`, `.portee` of an entry (read-only) | `.kind`, `.summary`, `.reach` <!-- lang: fr-ok --> |
| `.empreinte` of an entry | `.digest` <!-- lang: fr-ok --> |
| `entrees()` | `entries()` <!-- lang: fr-ok --> |
| `entree(slug)` | `entry(slug)` (reads `entries()`, so a patch of `entries` or `entrees` is seen) <!-- lang: fr-ok --> |
| `skills_du_catalogue()` | `catalogue_skills()` <!-- lang: fr-ok --> |
| `fichiers_de_la_skill(nom)` | `skill_files(name)` <!-- lang: fr-ok --> |
| `_contexte()` (private) | `_context()` <!-- lang: fr-ok --> |
| `_contexte.md` | `_context.md` (content unchanged: every digest is the same) <!-- lang: fr-ok --> |

Unchanged: `Catalogue`, `CatalogueSkill`, `EvalCase`, `catalogue`, `entry_of`, `load`, `of_plugin`,
`offline_directory`, `evaluate`, `cases`, `AgentEvalResult`.

The catalogue is now only the loader and `_context.md`: the 23 agents and 6 clients left the core in
S43-09, so there is no YAML left to translate in this package.

#### S45-07: the workflow DSL, gates, policy, instructions and presets in English

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `choregos_core.dsl.exigences` (`Exigence`, `exigences`, `ORDRE`, `ROLES_DANS_UN_DEPOT`, `ROLES_EN_PRODUCTION`) | `choregos_core.dsl.requirements` (`Requirement`, `requirements`, `ORDER`, `ROLES_IN_A_REPOSITORY`, `ROLES_IN_PRODUCTION`) | Nothing: an internal module path, a shim keeps it importable. <!-- lang: fr-ok --> |
| `Exigence.capacite`, `Exigence.raisons` | `Requirement.capability`, `Requirement.reasons` | Nothing: internal attributes, read-only aliases keep working. <!-- lang: fr-ok --> |
| `choregos_core.dsl.trains`: `approbation_du_train`, `env_du_train`, `train_apres_fusion`, `prend_le_train`, `ApprobationDuTrain` | `train_approval`, `train_env`, `train_after_merge`, `takes_the_train`, `TrainApproval` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `choregos_core.dsl.dates`: `DateIllisible`, `champ_de`, `echeance` | `UnreadableDate`, `field_of`, `due_date` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `choregos_core.dsl.edition`: `EditionRefusee`, `editer`, `PREFIXES_A_EFFET`, `Edition.avertissements` | `EditRefused`, `edit`, `PREFIXES_WITH_EFFECT`, `Edition.warnings` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `choregos_core.gates`: `ACTION_REGLEE`, `MOTIF_ADR` | `ACTION_SETTLED`, `ADR_PATTERN` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `choregos_core.instructions`: `rendre_les_instructions`, `erreurs_des_instructions`, `VARIABLES_D_EXEMPLE` | `render_instructions`, `instruction_errors`, `SAMPLE_VARIABLES` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `choregos_core.dsl.oublier_les_templates`, `validator.GATES_A_MATIERE`, `policy.CLES_D_APPROBATION` | `forget_templates`, `GATES_NEEDING_PARAMS`, `APPROVAL_KEYS` | Nothing: no importer outside core, renamed without an alias. <!-- lang: fr-ok --> |

Three messages are now English: the unknown-template error of `template_path`
(`unknown workflow template: <name> (known: ...)`), the unknown-preset error of `load_preset`
(`unknown policy preset: <name> (known: ...)`) and the unreadable-date error of
`choregos_core.dsl.dates` (`unreadable date expression: ...`). Nothing in the platform matches
them; a script that matched the French text must read the English one. Issue codes, DSL keywords
and JSON keys are unchanged.

The comments of the shipped templates and presets are English; their parsed content (and so the
workflow fingerprint) is unchanged.

#### Python names (internal: aliases keep working, nothing to do) (S45-08)

| Former | New |
| :-- | :-- |
| `choregos_core.acces` (`Acces`, `RapportAcces`, `rapport_d_acces`) | `choregos_core.access` (`AccessLine`, `AccessReport`, `access_report`) |
| `Acces.cible`, `.demandes`, `.refus`, `.motifs`, `.refuse` | `AccessLine.target`, `.requests`, `.refusals`, `.reasons`, `.refused` |
| `RapportAcces.acces`, `.evenements`, `.deduites`, `.refuses`, `.par_nature()` | `AccessReport.lines`, `.events`, `.inferred`, `.refused_lines`, `.by_nature()` |
| `choregos_core.sorties.explication` | `choregos_core.exit_codes.explanation` |
| `mcp.ERREUR_DE_SYNTAXE`, `REQUETE_INVALIDE`, `METHODE_INCONNUE`, `PARAMETRES_INVALIDES`, `ERREUR_INTERNE` | `PARSE_ERROR`, `INVALID_REQUEST`, `METHOD_NOT_FOUND`, `INVALID_PARAMS`, `INTERNAL_ERROR` (codes unchanged) |
| `mcp.negocier`, `version_acceptable`, `erreur`, `resultat_texte`, `est_notification`, `defaut_du_message` | `negotiate`, `acceptable_version`, `error`, `text_result`, `is_notification`, `message_defect` |
| `resultat_texte(en_erreur=, structure=)` | `text_result(is_error=, structured=)` (the former keywords are still accepted) |
| `catalogue.AppelHttp`, `AppelMcp`, `OutilCatalogue` | `HttpCall`, `McpCall`, `CatalogueTool` |
| `OutilCatalogue.ouvert_a`, `Catalogue.outils`, `Catalogue.par_nom` | `CatalogueTool.open_to`, `Catalogue.tools`, `Catalogue.by_name` |
| `catalogue.OutilInconnuError`, `ArgumentRefuseError`, `valider_les_arguments`, `construire_requete`, `charger_catalogue`, `GABARIT` | `UnknownToolError`, `ArgumentRefusedError`, `validate_arguments`, `build_request`, `load_catalogue`, `PLACEHOLDER` |
| `secrets.est_une_reference_du_magasin`, `ReferenceInvalide`, `VariableRefusee`, `PATTERN_PAR_DEFAUT`, `verifier`, `resoudre`, `reinitialiser` | `is_store_reference`, `InvalidReference`, `RefusedVariable`, `DEFAULT_PATTERN`, `check`, `resolve`, `reset` |

`choregos_core` re-exports `Acces`, `RapportAcces`, `rapport_d_acces`, `OutilCatalogue`,
`charger_catalogue` and `construire_requete` as aliases of the English objects.

#### S45-09: the fakes, families, memory, market data, identity, documents and storage adapters in English

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `python -m choregos_adapters.fakes.serveur` | `python -m choregos_adapters.fakes.server` | Change the container command of the demo fakes when you next edit it (the chart switches in S45-33); the former command still serves (`alias:external`, removed in 0.23.0). <!-- lang: fr-ok --> |
| `CONNECTOR_DEMO_JETON` | `CONNECTOR_DEMO_TOKEN` | Rename it in your values or `.env`; the former variable is still read when the new one is unset, and warns (`alias:external`, removed in 0.23.0). <!-- lang: fr-ok --> |
| `choregos_adapters.fakes.rh` | `choregos_adapters.fakes.hr` | Nothing: an internal module path, a shim keeps it importable. <!-- lang: fr-ok --> |
| `choregos_adapters.fakes.serveur` | `choregos_adapters.fakes.server` | Nothing: a shim keeps it importable. <!-- lang: fr-ok --> |
| `choregos_adapters.fakes.cd_de_demo` | `choregos_adapters.fakes.demo_cd` | Nothing: a shim keeps it importable. <!-- lang: fr-ok --> |
| `choregos_adapters.familles` | `choregos_adapters.families` | Nothing: a shim keeps it importable. <!-- lang: fr-ok --> |
| `choregos_adapters.memory.lexicale` | `choregos_adapters.memory.lexical` | Nothing: a shim keeps it importable. <!-- lang: fr-ok --> |
| `FakeFournisseur`, `FakeTransporteur`, `FauxMetier` | `FakeSupplier`, `FakeCarrier`, `BusinessFake` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `Guichet`, `GuichetDistant`, `serveur_mcp(faux, jeton=)` | `Desk`, `RemoteDesk`, `mcp_server(fake, token=)` | Nothing: internal names, aliases (and the former `jeton=` keyword) keep working. <!-- lang: fr-ok --> |
| `FAUX_PAR_FAMILLE`, `OUTILS_DU_FOURNISSEUR`, `ANCIENS_OUTILS_DU_FOURNISSEUR` | `FAKES_BY_FAMILY`, `SUPPLIER_TOOLS`, `FORMER_SUPPLIER_TOOLS` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `FakeMdm(cle_attendue=)`, `.postes`, `.etat()`; `FakeSupplier.commandes`; `FakeCarrier.envois` | `expected_key=`, `.devices`, `.state()`; `.orders`; `.shipments` | Nothing: internal names, the former keyword and attributes keep working. <!-- lang: fr-ok --> |
| `ApplicationDeDemo(jeton=)`, `.faux`, `.annuaire`, `.serveurs` | `DemoApplication(token=)`, `.fakes`, `.directory`, `.servers` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `CdDeDemo`, `FakeCdDeDemo`, `OPERATIONS_CD_DE_DEMO` | `DemoCd`, `FakeDemoCd`, `DEMO_CD_OPERATIONS` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `FakeEntra(secret_attendu=)`, `.unite`, `.comptes`, `.groupes`, `.ajouter_compte(dans_l_unite=)` | `expected_secret=`, `.unit`, `.accounts`, `.groups`, `.add_account(in_unit=)` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `FakeNotifier.panne` | `FakeNotifier.failure` | Nothing: internal name, an alias keeps working. <!-- lang: fr-ok --> |
| `EntraIdentity.unite`, `HorsDeLUnite` | `EntraIdentity.unit`, `OutsideUnit` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `ModelesMemoire`, `LexicalMemory.modeles` | `MemoryModels`, `LexicalMemory.models` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `OPERATIONS_MDM`, `OPERATIONS_EXPEDITION`, `OPERATIONS_CONTROLE_D_ACCES`, `FAMILLES` | `MDM_OPERATIONS`, `SHIPPING_OPERATIONS`, `ACCESS_CONTROL_OPERATIONS`, `FAMILIES` | Nothing: internal names, aliases keep working. <!-- lang: fr-ok --> |
| `executer(operation, params)` of the demo desks, `EntraIdentity`, the market data providers and the netops adapters | `execute(operation, params)` | Nothing: the connector seam still calls `executer`, which is kept as a class alias until S45-10 switches its callers. <!-- lang: fr-ok --> |
| The Slack button "Ouvrir dans Choregos" and the error "ni `bot_token` ni `webhook_url` configurés" | "Open in Choregos" and "neither `bot_token` nor `webhook_url` is configured" | Nothing; a message you matched on, if any, now reads in English. <!-- lang: fr-ok --> |

The HR fake keeps answering the former tenant tool names (`commander_poste`, `suivi_commande`, the
`modele` parameter): they are tenant names, not renamed here. Two messages of the fakes stay French
because tests outside this story pin them (`clé refusée`, `inscrit pour un autre utilisateur`; and
`projet inconnu pour la mémoire` of the lexical memory); they are translated with the tests that
match them. <!-- lang: fr-ok -->

#### S45-10: the connector registry, the extension loader, trackers, executors, gateways and SCM in English

Every former name below keeps working: a module path through a shim, a name through a typed alias,
and the names that tests patch (`FAUX_ENTRA`, `FAUX_MARKETDATA`, `FAUX_WEB_SEARCH`,
`charger_les_greffons`, `GROUPE_DE_GREFFONS`) through `choregos_core.compat.alias_names`: reading,
patching and undoing the former name reach the English one. The aliases are internal
(`alias:internal`); an operator has nothing to change. <!-- lang: fr-ok -->

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `choregos_adapters.charger_les_greffons()` | `choregos_adapters.load_extensions()` | Nothing: the former name still loads the extensions (`apps/api` and the orchestrator worker keep calling it until their own stories switch). <!-- lang: fr-ok --> |
| `choregos_adapters.GROUPE_DE_GREFFONS` | `choregos_adapters.EXTENSION_GROUP` | Nothing. Its value is still `choregos.plugins`: the entry-point group never changes (ADR 0093, ADR 0098 §7). <!-- lang: fr-ok --> |
| `choregos_adapters.FAUX_ENTRA`, `FAUX_MARKETDATA`, `FAUX_WEB_SEARCH` | `FAKE_ENTRA`, `FAKE_MARKETDATA`, `FAKE_WEB_SEARCH` | Nothing: the process-wide fakes; patching a former name patches the English one. <!-- lang: fr-ok --> |
| `choregos_adapters.POLITIQUES`, `registry.TYPES_PAR_DEFAUT`, `type_par_defaut`, `configuration_resolue`, `references_de_secret`, `schema_d_entree` | `POLICIES`, `DEFAULT_TYPES`, `default_type`, `resolved_configuration`, `secret_references`, `build_input_schema` | Nothing: internal names, typed aliases keep working. <!-- lang: fr-ok --> |
| `choregos_adapters.base.CAPACITES_EXECUTEUR` | `EXECUTOR_CAPABILITIES` | Nothing: an internal name, an alias keeps working. <!-- lang: fr-ok --> |
| `choregos_adapters.tracker.interne` | `choregos_adapters.tracker.internal` | Nothing: a shim keeps the former path importable. <!-- lang: fr-ok --> |
| `choregos_adapters.tracker.champs` (`COUT`, `TAILLE`, `RISQUE`, `ANCIENS_NOMS`, `NOMS_ACTUELS`, `nom_actuel`, `nom_present`) | `choregos_adapters.tracker.fields` (`COST`, `SIZE`, `RISK`, `FORMER_NAMES`, `CURRENT_NAMES`, `current_name`, `present_name`) | Nothing: a shim keeps the former path importable, with `nom_actuel` and `nom_present`. The French board field names (`Coût (€)`, `Taille`, `Risque`) stay in `FORMER_NAMES`, as data: they are names a tracker still carries. <!-- lang: fr-ok --> |
| `tests`: `Fil`, `sans_attente`, `.requetes`, `.corps()`, `.envoyees()` of `packages/adapters/tests/_transport.py` | `Wire`, `no_wait`, `.requests`, `.body()`, `.sent()` | Nothing: test helpers; the former names are kept for the tests of other stories (`test_ecphoria.py`). <!-- lang: fr-ok --> |

Messages that were French and are now English (an operator who matched one now reads English):

| Former | New |
| :-- | :-- |
| `en attente d'une place (plafond N par namespace)` (a queued Job's status message) | `waiting for a slot (cap N per namespace)` (as ADR 0013 already documents) <!-- lang: fr-ok --> |
| `runner sorti en N (...)`, `runner jamais démarré : ...`, `aucun pod du run : ...` | `runner exited with N (...)`, `runner never started: ...`, `no pod for the run: ...` <!-- lang: fr-ok --> |
| `connecteur `gateway: direct` refusé en prod : ...`, `connecteur `<famille>: demo` refusé en ...` | `connector `gateway: direct` refused in prod: ...`, `connector `<family>: demo` refused in ...` <!-- lang: fr-ok --> |
| `aucun adaptateur `x` pour `kind` (disponibles : ...)` | `no adapter `x` for `kind` (available: ...)` <!-- lang: fr-ok --> |
| `greffon « x » (...) : chargement impossible` | `extension "x" (...): cannot load` <!-- lang: fr-ok --> |
| the GitOps promotion PR body (`Promotion du lot ... vers ...`) | `Promotion of the batch ... to ...` <!-- lang: fr-ok --> |

What does not change, because it is stored or wired: the connector type ids (`plugin`, `github-issues`,
`jira`, ...), the config and secret field names, the `choregos.plugins` entry-point group, the
`greffon:` cost-ledger prefix and the `ecriture` extension tool key. <!-- lang: fr-ok -->

#### S45-11: the runner in English

The runner (`packages/runner`) is English. Every name below is internal: the former module keeps
importing and nothing changes for an operator. No environment variable, ACP method or persisted
key was renamed.
<!-- lang: fr-ok -->

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `choregos_runner.outils_locaux` | `choregos_runner.local_tools` | Nothing: the former module is a shim. <!-- lang: fr-ok --> |
| `outils_locaux.demarrer_si_absent`, `arreter` | `local_tools.start_if_absent`, `stop` | Nothing: the former names are aliases. <!-- lang: fr-ok --> |
| `choregos_runner.skills.poser`, `SkillInvalide`, `REPLI` | `place_skills`, `InvalidSkill`, `FALLBACK_DIR` | Nothing: used only inside the runner. <!-- lang: fr-ok --> |
| `choregos_runner.runner.RAPPORT_DEPART`, `ARBRE_VIDE` | `REPORT_START_COMMIT`, `EMPTY_TREE` | Nothing: used only inside the runner. <!-- lang: fr-ok --> |
| `choregos_runner.exits` (read `choregos_core.sorties`) | reads `choregos_core.exit_codes` | Nothing. <!-- lang: fr-ok --> |

The run-event key `demarre` of the `tools.local` journal event is written `started` since S45-39
(see [Persisted keys and error types](#persisted-keys-and-error-types-s45-39)).
<!-- lang: fr-ok -->

#### S45-12: API root modules, edition extension API, Principal and configuration in English

The API root modules are English. The five renamed modules keep their former dotted path: each
former module is the same module object as the new one (`importlib.import_module("choregos_api.fraicheur")
is choregos_api.freshness`), so a patch on one is a patch on the other. The edition extension API
(`choregos_api.edition`) is an `alias:external` window: every former name keeps working until 0.23.0
and warns with a `DeprecationWarning` that names its replacement. The other former names below are
internal and do not warn. Settings fields and environment variables did not change.
<!-- lang: fr-ok -->

#### Modules (S45-12)

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `choregos_api.adaptateurs` | `choregos_api.adapter_wiring` | Nothing: the former module is a shim. <!-- lang: fr-ok --> |
| `choregos_api.amorcage` | `choregos_api.bootstrap` | Nothing: the former module is a shim. <!-- lang: fr-ok --> |
| `choregos_api.fraicheur` | `choregos_api.freshness` | Nothing: the former module is a shim. <!-- lang: fr-ok --> |
| `choregos_api.limiteur` | `choregos_api.rate_limit` | Nothing: the former module is a shim. <!-- lang: fr-ok --> |
| `choregos_api.metriques` | `choregos_api.metrics` | Nothing: the former module is a shim. <!-- lang: fr-ok --> |

#### Names inside the root modules (internal, no warning) (S45-12)

| Former | New |
| :-- | :-- |
| `adaptateurs.brancher_memoire_lexicale` | `adapter_wiring.attach_lexical_memory` <!-- lang: fr-ok --> |
| `amorcage.amorcer` | `bootstrap.bootstrap_installation` (the returned keys are unchanged: `organisations`, `administrateurs`) <!-- lang: fr-ok --> |
| `fraicheur.age_de_l_authentification`, `exiger_une_authentification_fraiche` | `freshness.authentication_age`, `require_fresh_authentication` <!-- lang: fr-ok --> |
| `limiteur.Limiteur(par_minute)`, `.admet`, `installer`, `FENETRE_S` | `rate_limit.RateLimiter(per_minute)`, `.admits`, `install`, `WINDOW_S` (the keyword `par_minute=` still works) <!-- lang: fr-ok --> |
| `metriques.NOMS`, `ETATS_TRAIN`, `SECONDES_DEMARRAGE`, `SECONDES_LIVRAISON`, `ENVIRONNEMENTS` | `metrics.NAMES`, `TRAIN_STATES`, `START_SECONDS`, `DELIVERY_SECONDS`, `ENVIRONMENTS` <!-- lang: fr-ok --> |
| `metriques.registre`, `requetes_http`, `Instantane`, `CollecteurPlateforme` | `metrics.registry`, `http_requests`, `Snapshot`, `PlatformCollector` <!-- lang: fr-ok --> |
| `metriques.exposer`, `rafraichir`, `boucle_de_rafraichissement` | `metrics.expose`, `refresh`, `refresh_loop` <!-- lang: fr-ok --> |
| `catalogue.outils_du_projet`, `appeler`, `vider_cache` | `catalogue.project_tools`, `call`, `clear_cache` <!-- lang: fr-ok --> |
| `deps.get_db_plateforme`, `DbPlateforme` | `deps.get_platform_db`, `PlatformDb` <!-- lang: fr-ok --> |
| `deps.jeton_rest`, `noter_l_usage` | `deps.is_rest_token`, `record_usage` <!-- lang: fr-ok --> |
| `deps.exiger_admin_de_plateforme`, `_valider_la_session` | `deps.require_platform_admin`, `_validate_session` <!-- lang: fr-ok --> |
| `events.diffuser`, `CANAL`, `TAILLE_MAX` | `events.broadcast`, `CHANNEL`, `MAX_PAYLOAD_BYTES` (the PostgreSQL channel value `choregos_events` is unchanged) <!-- lang: fr-ok --> |
| `events.RelaisPostgres` with `.demarrer()`, `.arreter()`, `.recus` | `events.PostgresRelay` with `.start()`, `.stop()`, `.received` <!-- lang: fr-ok --> |
| `temporal._cause_lisible` | `temporal._readable_cause` <!-- lang: fr-ok --> |
| `main._verifier_l_edition` | `main._check_the_edition` <!-- lang: fr-ok --> |

#### S45-14: the inbound MCP door in English

`choregos_api.mcp` (ADR 0030) is English. The three renamed modules keep their former dotted path:
each former module is the same module object as the new one, so a patch on one is a patch on the
other. Every name the A2A door and the tests of other stories import keeps a typed alias. MCP tool
names, scopes, JSON-RPC errors, HTTP statuses and the persisted audit keys (`jeton`, `ecriture`, the
value `refus`) did not change. The two log keys of the `mcp.call` event are now `tool` and `caller`
(they were `outil` and `appelant`). Nothing here is an operator setting.
<!-- lang: fr-ok -->

#### Modules (S45-14)

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `choregos_api.mcp.appelant` | `choregos_api.mcp.caller` | Nothing: the former module is a shim. <!-- lang: fr-ok --> |
| `choregos_api.mcp.garde_fous` | `choregos_api.mcp.limits` | Nothing: the former module is a shim. <!-- lang: fr-ok --> |
| `choregos_api.mcp.outils` | `choregos_api.mcp.tools` | Nothing: the former module is a shim. <!-- lang: fr-ok --> |

#### Names (internal, no warning) (S45-14)

| Former | New |
| :-- | :-- |
| `Appelant(jeton, portees, cle, projet_lie, org_du_projet_lie, motifs)`, `.ecrit` | `Caller(token, scopes, key, bound_project, bound_project_org, reasons)`, `.writes` (the former keywords and attributes still work) <!-- lang: fr-ok --> |
| `Refus(statut, message, erreur=)`, `.statut`, `.erreur` | `Refusal(status, message, error=)`, `.status`, `.error` (the former ones still work) <!-- lang: fr-ok --> |
| `PORTEES_MCP`, `identifier`, `incarner_l_agent` | `MCP_SCOPES`, `identify`, `act_as_agent` <!-- lang: fr-ok --> |
| `APPELS_PAR_MINUTE`, `ECRITURES_PAR_JOUR`, `TAILLE_MAX`, `LIMITEUR`, `tronquer`, `ecritures_du_jour` | `CALLS_PER_MINUTE`, `WRITES_PER_DAY`, `MAX_SIZE`, `RATE_LIMITER`, `truncate`, `writes_today` <!-- lang: fr-ok --> |
| `Outil(nom, titre, ..., ecriture, executer)`, `OutilRefuse`, `Contexte`, `Executeur`, `annonces`, `outils` | `Tool(name, title, ..., write, execute)`, `ToolRefused`, `Context`, `Executor`, `announced_tools`, `core_tools` <!-- lang: fr-ok --> |
| `_outils_des_greffons`, `_executeur_de_greffon` | `_extension_tools`, `_extension_executor` (private) <!-- lang: fr-ok --> |
| `oauth.emetteur`, `metadonnees`, `valider`, `JetonRefuse`, `CLES`, `ALGORITHMES`, `DUREE_DU_CACHE` | `oauth.issuer_of`, `metadata`, `validate`, `TokenRefused`, `KEYS`, `ALGORITHMS`, `CACHE_TTL_S` (only `emetteur` keeps an alias) <!-- lang: fr-ok --> |
| `transport.TAILLE_MAX_DU_CORPS`, `_origines_admises`, `_refus_http(erreur_oauth=, chemin=)` | `MAX_BODY_SIZE`, `_allowed_origins`, `_http_refusal(oauth_error=, path=)` (the former keywords still work) <!-- lang: fr-ok --> |

Unchanged: `SCOPES_A2A`, `caller_of_token`, the audit keys `jeton` and `ecriture` and the audit value
`refus` (ADR 0098 §4, S45-39 switches them), the entry-point group value `choregos.plugins`.
<!-- lang: fr-ok -->

#### S45-15: the agent, skill, auth, project and work-item routers are English

Routes, operation ids, request and response fields, error codes and persisted audit keys did not
change. The OpenAPI descriptions that FastAPI takes from the routers' docstrings are now English.

#### Names (internal, no warning) (S45-15)

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `routers.auth.oublier_la_decouverte` | `forget_discovery` | Nothing: the former name is an alias. <!-- lang: fr-ok --> |
| `routers.auth.roles_des_groupes` | `roles_from_groups` | Nothing: the former name is an alias (an edition's group mapper receives the same function). <!-- lang: fr-ok --> |
| the routers' imports of `exiger_admin_de_plateforme`, `DbPlateforme`, `mappeur_de_groupes`, `est_entreprise`, `exiger_une_authentification_fraiche`, `outils_du_projet`, `choregos_api.greffons`, `DemandeDeGeste`, `controler`, `sections_declarees` | `require_platform_admin`, `PlatformDb`, `group_mapper`, `is_enterprise`, `require_fresh_authentication`, `project_tools`, `choregos_api.extensions`, `GestureRequest`, `check_gesture`, `declared_sections` | Nothing: the routers read the English names that S45-04 and S45-12 introduced. <!-- lang: fr-ok --> |

#### S45-17: the action, agent, definition, template and installation services are English

Routes, operation ids, response fields, error codes and every persisted value did not change.
Every former name below keeps working: it is an alias in the module that defines the new name, and
the seven renamed modules leave a shim at their former path that is the same module object, so a
patch on one is a patch on the other. Nothing here needs an operator action. Importers (the
orchestrator, the ontology, migrations, the broker, `services/__init__.py`, tests) were not switched:
they still use the former names.

#### Modules (`choregos_api.services`) (S45-17)

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `couts` | `costs` | Nothing: the former path is a shim. <!-- lang: fr-ok --> |
| `evenements` | `events` | Nothing: the former path is a shim. <!-- lang: fr-ok --> |
| `memoire` | `memory` | Nothing: the former path is a shim. <!-- lang: fr-ok --> |
| `projets` | `projects` | Nothing: the former path is a shim. <!-- lang: fr-ok --> |
| `gabarits` | `templates` | Nothing: the former path is a shim. <!-- lang: fr-ok --> |
| `catalogue_d_agents` | `agent_catalogue` | Nothing: the former path is a shim (it re-exports `DIVERGE`). <!-- lang: fr-ok --> |
| `connecteurs` | `connectors` | Nothing: the former path is a shim. <!-- lang: fr-ok --> |

#### Names (S45-17)

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `agents.AgentIndisponible` | `AgentUnavailable` (the Temporal error type string stays `AgentIndisponible` until S45-39) | Nothing: alias. <!-- lang: fr-ok --> |
| `agents.AgentResolu` | `ResolvedAgent` | Nothing: alias. <!-- lang: fr-ok --> |
| `agents.empreinte` | `digest` (algorithm and output unchanged: it computes `agent_versions.checksum`) | Nothing: alias, migration `20261016_1500` still imports the former name. <!-- lang: fr-ok --> |
| `agents.erreurs_d_une_version` | `version_errors` | Nothing: alias. <!-- lang: fr-ok --> |
| `agents.elargissements` | `widenings` | Nothing: alias. <!-- lang: fr-ok --> |
| `agents.resoudre_l_agent` | `resolve_agent` | Nothing: alias. <!-- lang: fr-ok --> |
| `agents.depense_depuis`, `depense_du_jour`, `depense_du_mois` | `spend_since`, `spend_today`, `spend_this_month` | Nothing: aliases. <!-- lang: fr-ok --> |
| `actions.rang`, `RANGS` | `rank`, `RANKS` | Nothing: aliases (the ontology imports them). <!-- lang: fr-ok --> |
| `actions.politique_de_l_action` | `action_policy` | Nothing: alias. <!-- lang: fr-ok --> |
| `actions.proposer(origine=, propose_par=)` | `propose(origin=, proposed_by=)` | Nothing: the former function is a wrapper that keeps the former keywords. <!-- lang: fr-ok --> |
| `actions.proposer_pour_une_transition(tentative=)` | `propose_for_transition(attempt=)` | Nothing: the former function is a wrapper that keeps the former keyword (the orchestrator's activity calls it so). <!-- lang: fr-ok --> |
| `actions.approuvee_par_la_politique` | `approved_by_policy` | Nothing: alias. <!-- lang: fr-ok --> |
| `actions.signaler_une_preuve` | `signal_evidence` | Nothing: alias. <!-- lang: fr-ok --> |
| `actions.decider` | `decide` | Nothing: alias. <!-- lang: fr-ok --> |
| `definitions.publier_workflow(nom_attendu=, devient_le_defaut=, creation_seule=)` | `publish_workflow(expected_name=, becomes_default=, create_only=)` | Nothing: the former function is a wrapper that keeps the former keywords. <!-- lang: fr-ok --> |
| `definitions.workflow_actif` | `active_workflow_named` (`active_workflow` is another function and stays) | Nothing: alias. <!-- lang: fr-ok --> |
| `definitions.workflow_du_ticket` | `work_item_workflow` | Nothing: alias. <!-- lang: fr-ok --> |
| `definitions.derniere_version` | `latest_version` | Nothing: alias that forwards a patch. <!-- lang: fr-ok --> |
| `definitions.signaler_ce_que_la_politique_n_applique_pas` | `report_unenforced_policy` | Nothing: alias. <!-- lang: fr-ok --> |
| `installation.installer_ce_que_livre_le_gabarit` | `install_what_template_delivers` | Nothing: alias. <!-- lang: fr-ok --> |
| `installation.installer_skills_et_agents(quoi=, du_catalogue=)` | `install_skills_and_agents(what=, catalogue_provenance=)` | Nothing: the former function is a wrapper that keeps the former keywords (`plugin_installer` calls it so). <!-- lang: fr-ok --> |
| `templates.Livraison`, fields `politique`, `defaut`, `routage`, `agents_du_catalogue` | `Delivery`, fields `policy`, `default`, `routing`, `catalogue_agents` | Nothing for the class and the functions (aliases); the renamed fields have no alias (internal dataclass). <!-- lang: fr-ok --> |
| `templates.livraison` | `delivery` | Nothing: alias that forwards a patch. <!-- lang: fr-ok --> |
| `templates.manifeste_du_gabarit` | `template_manifest` | Nothing: alias. <!-- lang: fr-ok --> |
| `templates.repertoire_des_gabarits` | `templates_directory` | Nothing: alias. <!-- lang: fr-ok --> |
| `templates.WORKFLOW_SANS_GABARIT`, `POLITIQUE_SANS_GABARIT`, `PREFIXE_CATALOGUE` | `WORKFLOW_WITHOUT_TEMPLATE`, `POLICY_WITHOUT_TEMPLATE`, `CATALOGUE_PREFIX` | Nothing: aliases. <!-- lang: fr-ok --> |
| `agent_catalogue.EtatDansLOrganisation` (fields `installe`, `version_installee`, `du_catalogue`, `mise_a_jour`) | `OrgState` (fields `installed`, `installed_version`, `catalogue_agent`, `update_available`) | Nothing for the class (alias); the fields have no alias, `routers/agents.py` reads the new ones. <!-- lang: fr-ok --> |
| `agent_catalogue.etat`, `offre`, `installer`, `mettre_a_jour`, `auteur`, `spec_de`, `PREFIXE` | `state`, `offer`, `install`, `upgrade`, `author_of`, `spec_of`, `PREFIX` | Nothing: aliases. <!-- lang: fr-ok --> |
| `agent_catalogue.agents_nommes`, `installer_les_agents_nommes` | `named_agents`, `install_named_agents` | Nothing: aliases. <!-- lang: fr-ok --> |
| `connectors.RANG`, `plus_stricte`, `spec_du_type`, `verifier_les_secrets` | `RANK`, `stricter`, `type_spec`, `verify_secrets` | Nothing: aliases. <!-- lang: fr-ok --> |
| `connectors.politique_pour_le_projet` | `policy_for_project` | Nothing: alias. <!-- lang: fr-ok --> |
| `memory.MIN_TICKETS_PAR_GROUPE` | `MIN_WORK_ITEMS_PER_GROUP` | Nothing: alias. <!-- lang: fr-ok --> |
| `agent_lifecycle.unused(agent, dernier, ...)` | `unused(agent, last, ...)` | Nothing for a positional call. <!-- lang: fr-ok --> |

#### S45-18: the work item, journey, receipt, routing and skill services are English

Routes, operation ids, response fields, error codes, timeline sentences and every persisted value
did not change. Every former name below keeps working: it is an alias in the module that defines the
new name (and in `choregos_api.services`), and the three renamed modules leave a shim at their former
path that is the same module object, so a patch on one is a patch on the other. Nothing here needs an
operator action. Importers (the orchestrator, the routers, the MCP door, the knowledge distiller,
`plugin_installer`, tools, tests) were not switched: they still use the former names.

#### Modules (`choregos_api.services`) (S45-18)

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `routage` | `routing` | Nothing: the former path is a shim. <!-- lang: fr-ok --> |
| `recus` | `receipts` | Nothing: the former path is a shim. <!-- lang: fr-ok --> |
| `parcours` | `journey` | Nothing: the former path is a shim. <!-- lang: fr-ok --> |

#### Names (S45-18)

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `routing.Naissance` | `WorkItemBirth` | Nothing: alias. <!-- lang: fr-ok --> |
| `routing.nouveau_ticket` | `new_work_item` | Nothing: alias that forwards a patch. <!-- lang: fr-ok --> |
| `routing.nouveau_ticket_d_essai` | `new_trial_work_item` | Nothing: alias. <!-- lang: fr-ok --> |
| `routing.verifier_la_naissance` | `verify_birth` | Nothing: alias. <!-- lang: fr-ok --> |
| `routing.valider_les_champs` | `validate_fields` | Nothing: alias. <!-- lang: fr-ok --> |
| `routing.choisir_workflow` | `choose_workflow` | Nothing: alias. <!-- lang: fr-ok --> |
| `routing.noter_les_etiquettes` | `record_labels` | Nothing: alias. <!-- lang: fr-ok --> |
| `receipts.PRODUITE`, `LUE` | `PRODUCED`, `READ` (the values `produced` and `read` are persisted and unchanged) | Nothing: aliases. <!-- lang: fr-ok --> |
| `receipts.DOCUMENTS_DU_PROMPT` | `PROMPT_DOCUMENTS` | Nothing: alias. <!-- lang: fr-ok --> |
| `receipts.empreinte` | `digest` (algorithm and output unchanged: `sha256:` of the canonical JSON) | Nothing: alias. <!-- lang: fr-ok --> |
| `receipts.consigner(sorties=)` | `record(outputs=)` | Nothing: the same function also accepts the former keyword `sorties`. <!-- lang: fr-ok --> |
| `receipts.lu_par_le_prompt` | `read_by_the_prompt` | Nothing: alias. <!-- lang: fr-ok --> |
| `receipts.passages_du_ticket` | `work_item_handoffs` | Nothing: alias. <!-- lang: fr-ok --> |
| `journey.parcours` | `journey` | Nothing: alias (`services.parcours` is still the function). <!-- lang: fr-ok --> |
| `journey.arete_du_deplacement` | `move_edge` | Nothing: alias. <!-- lang: fr-ok --> |
| `tickets.cle_de_ticket_interne` | `internal_work_item_key` | Nothing: alias that forwards a patch. <!-- lang: fr-ok --> |
| `tickets.le_tracker_est_interne` | `tracker_is_internal` | Nothing: alias that forwards a patch. <!-- lang: fr-ok --> |
| `tickets.tracker_du_projet` | `project_tracker` | Nothing: alias that forwards a patch (the tests that put a `FakeTracker` in its place keep working). <!-- lang: fr-ok --> |
| `tickets.sorties_a_ranger` | `outputs_to_store` | Nothing: alias. <!-- lang: fr-ok --> |
| `tickets.ranger_les_sorties` | `store_outputs` | Nothing: alias. <!-- lang: fr-ok --> |
| `tickets.creer_un_ticket(cle=)` | `create_work_item(key=)` | Nothing: alias, and the same function also accepts the former keyword `cle`. <!-- lang: fr-ok --> |
| `tickets.chronologie` | `timeline` | Nothing: alias. <!-- lang: fr-ok --> |
| `tickets.DOCUMENTS_LOGICIELS` | `SOFTWARE_DOCUMENTS` | Nothing: alias. <!-- lang: fr-ok --> |
| `tickets._statut_temporal` | `_temporal_status` | Nothing: alias. <!-- lang: fr-ok --> |
| `skills.SkillRefusee` | `SkillRefused` | Nothing: alias (the same class: `except SkillRefusee` still catches it). <!-- lang: fr-ok --> |
| `skills.valider` | `validate` | Nothing: alias. <!-- lang: fr-ok --> |
| `skills.empreinte` | `digest` (algorithm unchanged: the runner recomputes it) | Nothing: alias. <!-- lang: fr-ok --> |
| `skills.entete` | `front_matter` | Nothing: alias. <!-- lang: fr-ok --> |
| `skills.chemin_sur` | `safe_path` | Nothing: alias. <!-- lang: fr-ok --> |
| `skills.lire_le_zip` | `read_zip_files` | Nothing: alias. <!-- lang: fr-ok --> |
| `skills.Entete` | `FrontMatter` | Nothing: alias. <!-- lang: fr-ok --> |
| `skills.MAX_FICHIERS`, `MAX_OCTETS`, `NOM` | `MAX_FILES`, `MAX_BYTES`, `NAME` | Nothing: aliases. <!-- lang: fr-ok --> |

`choregos_api.services` re-exports the English names first, and keeps every former name in
`__all__` as an alias. `agent_releases` now imports the extension API by its English names
(`choregos_api.extensions`: `GestureRequest`, `check_gesture`). The `courtier` shim and the broker
were not touched.

#### S45-22: the orchestrator's workflows, queue routing, worker and configuration are English

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `choregos_orchestrator.repartition` | `choregos_orchestrator.queues` | Nothing: the former module path is a shim. <!-- lang: fr-ok --> |
| `ACTIVITES_PAR_FILE`, `FILE_PAR_ACTIVITE`, `nom_de_l_activite`, `file_de` | `ACTIVITIES_BY_QUEUE`, `QUEUE_BY_ACTIVITY`, `activity_name`, `queue_of` | Nothing: the former names still work. The routing table itself did not change. <!-- lang: fr-ok --> |
| `choregos_orchestrator.workflows.planification` | `choregos_orchestrator.workflows.scheduling` | Nothing: the former module path is a shim. <!-- lang: fr-ok --> |
| `executer_activite`, `demarrer_activite` | `execute_on_its_queue`, `start_on_its_queue` | Nothing: the former names still work. <!-- lang: fr-ok --> |
| `OrchestratorSettings.url_du_sidecar_d_outils` | `OrchestratorSettings.tools_sidecar_url` | Nothing: a property, the former name still works. No environment variable changed. <!-- lang: fr-ok --> |
| `testing.workers_repartis`, `avec_doublures`, `repartir` | `workers_by_queue`, `with_doubles`, `group_by_queue` | Nothing: the former names still work, and `workers_by_queue` also accepts the former keywords `activites`, `file_du_workflow` and `remplacements`. <!-- lang: fr-ok --> |
| `gitops.PLAGES_PRIVEES` | `gitops.PRIVATE_RANGES` | Nothing: the former name still works. <!-- lang: fr-ok --> |
| `workflows.train.SEUIL_DE_RELAIS`, `SEUIL_DE_RELAIS_D_AVANT` | `RELAY_THRESHOLD`, `FORMER_RELAY_THRESHOLD` | Nothing. A test that patches the threshold patches the English name (the former `SEUIL_DE_RELAIS` stays readable only). <!-- lang: fr-ok --> |
| `TrainInput.seuil_au_repos_d_avant()` | `TrainInput.former_idle_threshold()` | Nothing: an internal name, an alias keeps working. <!-- lang: fr-ok --> |
| `workflows.train.LE_TRAIN_SE_RELAIE_TOT`, `LE_TRAIN_AU_REPOS_SE_RELAIE` | `TRAIN_CONTINUES_AS_NEW_EARLY`, `IDLE_TRAIN_CONTINUES_AS_NEW` | Nothing: Python names of two patch markers whose ids are unchanged; the former names still work. <!-- lang: fr-ok --> |
| `workflows.action.PreuveManquante`, `EFFET`, `LECTURE` | `MissingEvidence`, `EFFECT`, `READ` | Nothing: internal names (`EFFET` keeps an alias). <!-- lang: fr-ok --> |
| `workflows.interpreter.REPRISE_APRES_ESCALADE` | `RESUME_AFTER_ESCALATION` | Nothing: the former name still works; the patch id is unchanged. <!-- lang: fr-ok --> |

Nothing a running workflow or a recorded history names moved: the six `workflow.patched` ids
(`preuve-attendue`, `migration-par-definition`, `actions-de-transition`, `taches-humaines`,
`train-par-environnement`, `auto-sync-previent`), the workflow type names, the signal and query wire
names (including the French signal `preuve` of `ActionWorkflow`), the activity names, the task
queues, the workflow-id prefixes, the payload keys of ADR 0098 §4 (`cle`, `refus`, `conclure`,
`en_attente`, `jusqu_a`, `preuve`) and `TrainInput.seuil_de_relais`. Every history of
`tests/replay/histories` replays unchanged. The workflows' Python attributes (`self.state`,
`self.done`, `self.evidence_by_position`, `self.fields`, ...) are not serialized, and the query
outputs keep their keys. <!-- lang: fr-ok -->

The activities (`apps/orchestrator/src/choregos_orchestrator/activities/`) are not part of this
story: they are still partly French until S45-23.

#### S45-23: the orchestrator's activities are English, wire names unchanged

Every former name below keeps working: a module path through a shim (`replace_module`), a name
through a typed alias in the module that defined it. The aliases are internal (`alias:internal`);
an operator has nothing to change. <!-- lang: fr-ok -->

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `choregos_orchestrator.activities.bilan` | `choregos_orchestrator.activities.outcome` | Nothing: the former module path is a shim, the same module answers to both dotted names. <!-- lang: fr-ok --> |
| `choregos_orchestrator.activities.garde` (`garde_contre_l_injection`) | `choregos_orchestrator.activities.injection_guard` (`guard_against_injection`) | Nothing: the former module path is a shim. <!-- lang: fr-ok --> |
| `activities.base.workflow_du_ticket`, `tracker_possede_les_tickets`, `cle_de_ticket_interne` | `work_item_workflow`, `tracker_owns_items`, `internal_ticket_key` | Nothing: `workflow_du_ticket` keeps an alias; the two others were used only inside the activities. <!-- lang: fr-ok --> |
| `activities.execution.hotes_a_joindre_en_direct`, `env_du_runner`, `classe_d_execution`, `FILE_MAX_MINUTES` | `hosts_to_reach_directly`, `runner_environment`, `runtime_class`, `QUEUE_MAX_MINUTES` | Nothing: the former names still work, also through `activities.stage`. <!-- lang: fr-ok --> |
| `activities.gates.texte_ajoute` | `added_text_of` | Nothing: the former name still works. <!-- lang: fr-ok --> |
| `activities.interpretation.message_de`, `activite_de`, `BOUCHE_TROU_TEMPORAL` | `message_of`, `activity_of`, `TEMPORAL_PLACEHOLDERS` | Nothing: the two functions keep an alias (the workflows import them until their own story switches). <!-- lang: fr-ok --> |
| `activities.provisioning.repertoire_templates`, `etiquettes_du_pas`, `COULEUR_PAR_DEFAUT`, `CHAMPS_DU_BOARD` | `templates_dir`, `step_labels`, `DEFAULT_COLOUR`, `BOARD_FIELDS` | Nothing: the first two keep an alias. <!-- lang: fr-ok --> |
| `activities.stage._serveurs_mcp` | `_mcp_servers` | Nothing: an internal name, an alias keeps working. <!-- lang: fr-ok --> |
| `activities.stage` reads `settings.url_du_sidecar_d_outils` | `settings.tools_sidecar_url` (S45-22) | Nothing: no environment variable changed. <!-- lang: fr-ok --> |
| `choregos_adapters.charger_les_greffons`, `choregos_api.effets` names, `choregos_core.quotas.quota_pour` | `load_extensions`, `choregos_api.effects` (`EffectContext`, `EffectRefused`, `effect`, `render`), `quota_for` | Nothing: the activities now call the English names that already exist on main (S45-10, the effects module, S45-08). <!-- lang: fr-ok --> |

Messages that were French and are now English (an operator who matched one now reads English):

| Former | New |
| :-- | :-- |
| `projet inconnu : x`, `projet introuvable : x`, `action introuvable : x` (activity errors) | `unknown project: x`, `project not found: x`, `action not found: x` <!-- lang: fr-ok --> |
| `ticket non prévenu` and `demande humaine ... créée mais NON notifiée` (orchestrator log lines) | `work item not notified` and `human request ... created but NOT notified` <!-- lang: fr-ok --> |
| `revue croisée`, `revue croisée impossible` (log events, with the key `raison`) | `cross review`, `cross review impossible` (key `reason`) <!-- lang: fr-ok --> |
| The details of an eval assertion (`fichier attendu absent`, `motif présent : ...`, `outil absent sur ce runner`, `délai dépassé`, `dépôt jouet absent`, `aucune modification scriptée pour ...`) | `expected file absent`, `pattern present: ...`, `tool absent on this runner`, `timed out`, `toy repository absent`, `no scripted change for ...` <!-- lang: fr-ok --> |

What does not change, because it is recorded or wired (each such line carries `lang: fr-ok` and
`S45-39`): the 84 registered activity names (including the seven French aliases of `ActionWorkflow`
and `confirmer_auto_sync`), the raised error types `AgentIndisponible`, `InstructionsInvalides`,
`SkillAbsente`, `EffetRefuse`, `ActionIntrouvable` and `ActionPasEncoreVisible`, the written keys
`effets`, `statut`, `etats`, `cle` and `refus` (the result of `propose_transition_action` included)
and the `actions.result` keys `attente`, `preuves` and `jusqu_a`. Every history of
`tests/replay/histories` replays unchanged. <!-- lang: fr-ok -->

#### S45-48: the database layer, the A2A door, egress and knowledge in English

`choregos_api.db`, `a2a`, `egress` and `knowledge` are English. Tables, columns, indexes and
constraints did not change, and neither did the persisted audit keys of the A2A door (`jeton`,
`ecriture`, the value `refus`; S45-39 switches them). Every former name that another module imports
keeps a typed alias. Nothing here is an operator setting.
<!-- lang: fr-ok -->

#### Names (internal, no warning) (S45-48)

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `db.session.TOUT` | `ALL_ORGS` | Nothing: the former name is an alias. <!-- lang: fr-ok --> |
| `db.session.limiter_aux_organisations` | `limit_to_organisations` | Nothing: the former name is an alias. <!-- lang: fr-ok --> |
| `db.session.nommer_l_utilisateur`, `en_portee_de_plateforme` | `name_the_user`, `in_platform_scope` | Nothing: the former names are aliases. <!-- lang: fr-ok --> |
| `db.models.objet_du_coeur` | `is_core_object` | Nothing: the former name is an alias (Alembic `include_object`). <!-- lang: fr-ok --> |

### Log events, log keys and messages

Not aliased: a log filter, an alert on a log line or a saved query that matches the former text has to be updated. These changes do not prove that any ops log filter was updated.

#### Messages now in English (S45-08)

The JSON-RPC defect messages of `mcp.message_defect` ("a JSON-RPC message is an object",
"`jsonrpc` must be "2.0"", "`method` is missing"), the missing-argument error of a catalogue call
("missing argument: <key>") and the no-HTTP-source error of `build_request`. The access report keys,
the JSON-RPC codes and the catalogue's English fields are unchanged.

#### Logger names, log events and keys (ops log filters) (S45-12)

These are not aliased: a log filter, an alert on a log line or a saved query that matches them has
to be updated. This change does not prove that any ops log filter was updated.

| Former | New |
| :-- | :-- |
| logger `choregos.amorcage` | `choregos.bootstrap` <!-- lang: fr-ok --> |
| logger `choregos.metriques` | `choregos.metrics` <!-- lang: fr-ok --> |
| event `installation amorcée` (logger `choregos.amorcage`) | `installation bootstrapped` <!-- lang: fr-ok --> |
| event `démarrage de l'API`, `arrêt de l'API` | `API starting`, `API stopping` <!-- lang: fr-ok --> |
| event `requête` (one per HTTP request, logger `choregos.api`) | `request` <!-- lang: fr-ok --> |
| event `relais d'événements PostgreSQL branché`, key `canal` | `PostgreSQL event relay attached`, key `channel` <!-- lang: fr-ok --> |
| event `relais d'événements PostgreSQL perdu, reconnexion`, key `dans_s` | `PostgreSQL event relay lost, reconnecting`, key `retry_in_s` <!-- lang: fr-ok --> |
| event `notification illisible` | `unreadable notification` <!-- lang: fr-ok --> |
| event `train sans état`, key `projet` | `train without state`, key `project` <!-- lang: fr-ok --> |
| event `rafraîchissement des métriques raté` | `metrics refresh failed` <!-- lang: fr-ok --> |
| event `workflow déjà démarré` | `workflow already started` <!-- lang: fr-ok --> |

#### Texts that changed (no alias) (S45-12)

| Former | New |
| :-- | :-- |
| the `# HELP` lines of the Prometheus series (`/metrics`) were French | English; the series names, labels and types are unchanged <!-- lang: fr-ok --> |
| `choregos_api.__version__` of a tree that is not installed: `0.0.0+inconnue` | `0.0.0+unknown` <!-- lang: fr-ok --> |
| start-up refusals: the edition mismatch, the PostgreSQL superuser, `CHOREGOS_DEV_LOGIN_ENABLED` in staging or prod, the unknown `CHOREGOS_MCP_OAUTH_CLIENTS` clients | English (the message still names the setting and the fix) <!-- lang: fr-ok --> |

Still French on purpose, because a test or a page of `docs/deployment.md` matches them (the stories
of those tests and pages translate both sides):

- the refusal `réglages non résolus : [...]` (a setting whose value is `<no value>`); <!-- lang: fr-ok -->
- the refusal `CHOREGOS_RUN_TOKEN_PUBLIC_KEY est fournie sans sa clé privée`; <!-- lang: fr-ok -->
- the administration-section refusals `section d'administration ... invalide`, <!-- lang: fr-ok -->
  `permission inconnue` and `n'est servi par aucune route`; <!-- lang: fr-ok -->
- the extension-route overlap `un greffon déclare des routes déjà servies : ...`. <!-- lang: fr-ok -->

The settings field `courtier_attente_ecriture_s` (environment variable
`CHOREGOS_COURTIER_ATTENTE_ECRITURE_S`) keeps its French name: an environment variable is renamed
through `AliasChoices`, which is the broker story's. <!-- lang: fr-ok -->

#### Log events and keys (ops log filters) (S45-15)

These are not aliased: a log filter or an alert that matches them has to be updated. This change
does not prove that any ops log filter was updated.

| Former | New |
| :-- | :-- |
| event `champs non transmis à l'interpréteur`, key `erreur` (logger of `routers.workitems`) | `fields not sent to the interpreter`, key `error` <!-- lang: fr-ok --> |
| event `redirection refusée` (logger `choregos.auth`) | `redirect refused` <!-- lang: fr-ok --> |

#### Log events (ops log filters) (S45-17)

These are not aliased: a log filter or an alert that matches them has to be updated. This change does
not prove that any ops log filter was updated.

| Former | New |
| :-- | :-- |
| event `gabarit.extension_sans_greffon` (logger `choregos.gabarits`) | `template.extension_without_installer` (logger `choregos.templates`) <!-- lang: fr-ok --> |

The audit `reason` of `template.extension.skip` is now `no active extension installs it` (display
text, was `aucun greffon actif ne l'installe`).

#### Log keys (S45-48)

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `a2a.call` log key `appelant` | `caller` | Update a log query that reads the key. <!-- lang: fr-ok --> |

Unchanged: the audit keys `ecriture` and `jeton`, the audit value `refus`, the `a2a.call` event name.
<!-- lang: fr-ok -->

### Tests, histories and fixtures

Test modules, archived histories and eval fixtures. Nothing an operator runs changes except where a row says so.

#### S45-24: the orchestrator's tests a-h, the eval fixtures and the toy repository are English

Nothing an operator runs changes except the names of the eval fixtures, and every former name keeps
working: a stored matrix or a schedule that names one plays the ticket that replaced it
(`FORMER_FIXTURE_NAMES`, an external alias, removed in 0.23.0).

| Former | New | What an operator does |
| :-- | :-- | :-- |
| Eval fixtures `node-slug-longueur`, `py-bug-arrondi`, `py-hors-perimetre`, `py-refactor-sans-regression`, `py-remise-volume`, `py-test-manquant` <!-- lang: fr-ok --> | `node-slug-length`, `py-rounding-bug`, `py-out-of-scope`, `py-refactor-without-regression`, `py-volume-discount`, `py-missing-test` | Nothing: a matrix naming a former name still runs the renamed ticket. A live cell files its work item with the label `eval:<new name>`; an eval work item filed earlier keeps its former label. |
| `tests/fixtures/repos/python-toy/src/panier/` (`panier.py`, `Ligne`, `Panier`, `total_ttc`) and `tests/test_panier.py` <!-- lang: fr-ok --> | `src/cart/` (`cart.py`, `Line`, `Cart`, `total_with_tax`) and `tests/fixtures/repos/python-toy/tests/test_cart.py` | Nothing: the toy repository is committed to the eval project's branch by digest (`choregos-evals/python-toy-<digest>`), so the next live matrix commits the new tree on a new branch. |
| (new) | `tests/fixtures/tickets/py-rounding-bug-fr.yaml` | One reference ticket stays French on purpose: the agent answers in English (S22-03). It is played like the others. |
| `tests/replay/histories/wi-action-a-date-S20-05.json` <!-- lang: fr-ok --> | `wi-dated-action-S20-05.json` | Nothing: the file is renamed, its content is untouched, and it replays. |

The nine French-named test modules of the orchestrator are renamed with `git mv` and no redirect
(ADR 0098 §3 (g)): `test_action_a_date` to `test_dated_action`, `test_agent_du_registre` to
`test_registry_agent`, `test_besoins_du_gabarit` to `test_template_requirements`,
`test_board_en_anglais` to `test_board_in_english`, `test_effets_ecrits` to `test_written_effects`,
`test_epingle` to `test_pinned_definition`, `test_file_d_attente` to `test_queue_wait`,
`test_gabarit_livraison_logicielle` to `test_software_delivery_template` and `test_gabarits_du_coeur`
to `test_core_templates`. <!-- lang: fr-ok -->

#### S45-25: the orchestrator's tests i-z, the replay helpers and their histories are English

Nothing an operator runs changes: only test modules and archived histories are renamed. Every
history file is renamed with `git mv` and its content is untouched, so each one still replays.

| Former | New | What an operator does |
| :-- | :-- | :-- |
| `tests/replay/histories/action-preuve-S20-08.json` <!-- lang: fr-ok --> | `action-evidence-S20-08.json` | Nothing |
| `train-approbation-S21-21.json` <!-- lang: fr-ok --> | `train-approval-S21-21.json` | Nothing |
| `train-au-repos-S22-24.json` <!-- lang: fr-ok --> | `train-at-rest-S22-24.json` | Nothing |
| `train-ecoute-le-cd-S22-18.json` <!-- lang: fr-ok --> | `train-listens-to-cd-S22-18.json` | Nothing |
| `train-se-relaie-tot-S22-27.json` <!-- lang: fr-ok --> | `train-relays-early-S22-27.json` | Nothing |
| `wi-tache-badge-S20-06.json` <!-- lang: fr-ok --> | `wi-human-task-badge-S20-06.json` | Nothing |
| `train-express-ancien-code.json` <!-- lang: fr-ok --> | `train-express-old-code.json` | Nothing |
| `wi-migration-ancien-code.json` <!-- lang: fr-ok --> | `wi-migration-old-code.json` | Nothing |
| `wi-train-ancien-code.json` <!-- lang: fr-ok --> | `wi-train-old-code.json` | Nothing |

The four histories whose French stem is a recorded workflow id (`wi-panier-DEMO-1e`,
`wi-panier-DEMO-2d`, `wi-staffing-RH-1d`, `wi-staffing-RH-1f`) keep their name (ADR 0098 §4).

The fifteen French-named test modules of the orchestrator are renamed with `git mv` and no redirect
(ADR 0098 §3 (g)): `test_perimetre_du_run` to `test_run_perimeter`, `test_preuve_attendue` to
`test_awaited_evidence`, `test_rbac_du_projet` to `test_project_rbac`, `test_recus_du_prompt` to
`test_prompt_receipts`, `test_repartition_des_files` to `test_queue_routing`,
`test_reprise_apres_escalade` to `test_resume_after_escalation`, `test_sorties_d_etape` to
`test_step_outputs`, `test_tache_humaine` to `test_human_task`, `test_templates_livres` to
`test_shipped_templates`, `test_train_au_repos` to `test_train_at_rest`, `test_train_cd_de_demo` to
`test_train_demo_cd`, `test_train_ecoute_le_cd` to `test_train_listens_to_cd`, `test_train_et_ticket`
to `test_train_and_work_item`, `test_train_se_relaie_tot` to `test_train_relays_early` and
`test_worker_greffons` to `test_worker_extensions` (an entry-point package is an extension, ADR 0093).
<!-- lang: fr-ok -->

### Persisted keys and error types (S45-39)

The names that histories and rows record were born French. S45-03 made every reader accept both
spellings; from the release that carries S45-39, **everything written is English**. Nothing is
rewritten: a history recorded before still replays, a row written before is still read, and the
readers keep both spellings forever (do not remove a French one).

| Where | Written until now | Written from S45-39 |
| --- | --- | --- |
| Audit payload of `mcp.call` and `a2a.call` | `ecriture`, `jeton`, issue `refus` | `write`, `token`, issue `refused` |
| Audit payload of `org.update` | `avant`, `apres` | `before`, `after` |
| Installation result (audit details) | `sans_greffon` | `without_extension` |
| Run event `tools.local` | `demarre` | `started` |
| Event `choregos.workitem.migrated` and `.migration_refused` | `cle` | `key` |
| `action.result` | `attente` (`position`, `jusqu_a`), `preuves` | `waiting` (`position`, `until`), `evidence_received` |
| `action.params` of an ontology action | `ontologie` | `ontology` |
| Effect kinds of an ontology action | `ontology.effet`, `ontology.preuve` | `ontology.effect`, `ontology.evidence` |
| Announced MCP tool of an extension | `ecriture` | `write` |
| Activity returns | `effets`, `statut`, `etats`, `refus`, `attendre_une_preuve` (`jusqu_a`) | `effects`, `status`, `states`, `refusal`, `await_evidence` (`until`) |
| Temporal error types | `AgentIndisponible`, `InstructionsInvalides`, `SkillAbsente`, `EffetRefuse`, `ActionIntrouvable`, `ActionPasEncoreVisible` | `AgentUnavailable`, `InvalidInstructions`, `SkillMissing`, `EffectRefused`, `ActionNotFound`, `ActionNotYetVisible` |

What a **workflow sends** to an activity (`mark_awaiting_evidence`, `confirm_auto_sync`,
`record_migration`, and the relay threshold a train carries over a continue-as-new) changes behind
one patch, `english-payload-keys`: a workflow that started before keeps sending the French keys to
its end, one that starts after sends English ones. The activities read both. The three histories
`action-english-keys-S45-39`, `train-english-keys-S45-39` and `wi-migration-english-keys-S45-39`
archive the English side; the older ones archive the French side, and all of them replay.

Two things to know when you query these rows or watch these errors:

- An alert, a dashboard or a query on one of the audit keys, the issue value or an error type above
  must match **both** spellings for as long as rows or workflows written before exist.
- `action.result.evidence_received` is not `evidence`: an ontology action already keeps its own
  file of evidence under `result.evidence`, and the two must not mix.

### Kept on purpose

Names that are recorded, persisted or wired, so they were not renamed here.

#### Kept on purpose (S45-15)

- The audit payload keys `avant` and `apres` of `org.update` were kept until S45-39, which writes
  `before` and `after`.
- The audit key `canal` of `auth.login` and the keyword `canal` of `ouvrir_la_session`: persisted
  and shared with `services/tickets.py`.
- `routers.auth.terminer_la_connexion(nom=, sujet=, groupes=, cible=, canal=)`: the public API that
  an edition's SAML protocol calls. It is not an alias (it has no English name yet) and no window
  covers it: its name and keywords stay French until choregos-ee and the core rename it together
  (#1411).
- The CSV header `budget_quotidien_usd` of the cost export.

#### Kept on purpose (S45-17)

- The key `sans_greffon` of the installation result and the Temporal error type string
  `AgentIndisponible` were kept until S45-39, which writes `without_extension` and `AgentUnavailable`.
- The `plugin:` provenance prefix and the `source == "plugin"` label of ADR 0093.
- The verdict `organisation inconnue` of the memory A/B comparison (a returned value, S45-39).
- The French imports from other packages (`POLITIQUES`, `ReferenceInvalide`,
  `est_une_reference_du_magasin`, `verifier`, `erreurs_des_instructions`, `skills.empreinte`): their
  owners rename them.
