# Prompt, voice and hosted agents

Three shapes of agent whose code is not ours, or that have no code at all — the vocabulary of
Azure AI Foundry's Agent Service, so that an agent built for it runs here unchanged. The decision is
[ADR 0080](adr/0080-hosted-prompt-and-voice-agents.md).

| Runtime | What the version says | Runs as |
| :-- | :-- | :-- |
| `prompt` | instructions, a model, MCP servers and knowledge; no `backend` | a workflow step, a session |
| `voice` | the same, spoken: a realtime `model`, a `voice`, PCM16 audio; no `backend`, no `skills` | a session only |
| `hosted` | the agent's own container (`source: {type: image, ref}` or a source zip), serving Foundry's contract on port 8088; no `backend`, no `skills` | a workflow step, a session |

```json
{
  "instructions": "Triage {{ ticket.title }}.",
  "runtime": {
    "kind": "hosted",
    "source": { "type": "image", "ref": "ghcr.io/acme/triager:1.2" },
    "protocols": ["responses"],
    "workspace": "none"
  }
}
```

## What is delivered

**The contract (S41-01) and the image supply chain (S41-02).** The registry accepts and validates the
three runtimes; the stage input carries `hosted` and `prompt` steps. Nothing runs them yet:

- a hosted **image** is accepted only under a prefix of `CHOREGOS_HOSTED_REGISTRIES`
  (`global.images.hostedRegistries`, matched by path segment); outside it, the version is refused
  before anything is fetched. Publication (create, publish, trial, template install) resolves the tag
  to the digest the registry serves and writes it in the version; a digest the caller wrote is resolved
  again, never trusted; a restore keeps the digest it was published with. The manifest is read through
  the organisation's egress allowlist, anonymously;
- `CHOREGOS_HOSTED_COSIGN`: `enforce` refuses an image without a trusted signature, `warn` publishes it
  and logs `hosted.signature_unverified`, `off` (the default) does not ask. The verifier is cosign
  (S41-14, below);
- a hosted **zip** source names a zip uploaded first (S41-07, below); publication builds it (S41-08);
- a step whose agent is hosted stops before any key is minted, with the reason (`AgentIndisponible`,
  not retried); voice agents are never steps.

What the validation already refuses: a `backend` on any of the three, `skills` on voice and hosted,
a voice agent without a model, a hosted port on the runner's loopback listeners (4001-4003), a hosted
`env` naming a reserved variable (`PORT`, the proxy variables, anything under `CHOREGOS_`,
`FOUNDRY_`, `OPENAI_`, `ANTHROPIC_`, `AZURE_`), a protocol listed twice, a zip runtime the platform
has no base image for.

**A prompt agent carries a workflow step (S41-03).** The orchestrator names the `prompt` backend and
puts `StageInput.prompt` in the stage input; an actor that names another backend is refused. The
runner runs its own tool loop against the run's gateway model (chat completions, the run's key, which
goes to the gateway and nowhere else). The model is offered:

- `read_file` and `list_files`, read-only, inside the workspace (the task, the knowledge, the inputs);
- `write_output`, only when the step declares files, and only for those file names;
- `submit_result`, validated like an agent's `result.json`; a refused result goes back to the model;
- the tools of the step's MCP servers that have a `url`, named `server__tool`. Every call is judged by
  the run's guard rails and journaled as `session/request_permission`; a refusal goes back to the
  model. The runner reaches only the hosts of those servers and of `allow_domains`, never one of
  `deny_domains`. A stdio server is a process that a coding agent starts, so a prompt step skips it.

A tool the model names without being offered it is never called (`prompt.tool_refused`). Every model
call is a turn: at `max_turns` the step ends `failed` with reason `max_turns`. A spent key ends it on
`budget`. With no declared output, the model's final text is the result; under
`response_format: json_object`, its JSON is. A prompt step has no context compression and no DoD
iterations.

**A hosted agent carries a step through the runner's loopback listeners (S41-04).** When the stage
input carries `hosted`, the runner serves three listeners on `127.0.0.1` for the step's duration,
waits for the container (`GET /readiness`, up to `readiness_timeout_s`), sends it the task over
`POST /responses` (streamed events) or `POST /invocations`, and reads the answer:

- **the model, on `:4001`** (`OPENAI_BASE_URL=http://127.0.0.1:4001/v1`, `ANTHROPIC_BASE_URL`):
  chat completions, responses, messages, embeddings and models, forwarded to the run's gateway with
  the run's key and the run's model, whatever model the container names. Only the loopback key
  (`choregos-loopback`) is accepted. Every call is a turn: past `max_turns` the call is answered 400
  `max_turns` and never forwarded (`hosted.model_refused`); not 429, which every SDK retries (Claude
  Code until its own timeout, found by S41-06). A gateway budget refusal ends the step on `budget`;
- **the MCP servers, on `:4002`** (`CHOREGOS_MCP_URL=http://127.0.0.1:4002/mcp/`, then the server's
  name; `CHOREGOS_MCP_SERVERS` lists them): only the step's granted servers are served (any other is
  404, `hosted.mcp_refused`), only on a host the step's network allows. Each `tools/call` is judged by
  the run's guard rails and journaled as `session/request_permission`; a refusal answers a tool error
  and never reaches the server. The server's credential is added by the runner;
- **the internet, on `:4003`** (`HTTPS_PROXY`): an HTTP CONNECT proxy. A host of `deny_domains` is
  refused, so is a host outside `allow_domains` when the list applies; plain HTTP is refused (405).
  An accepted tunnel goes through the pod's egress proxy with the run's credentials
  (`hosted.egress`, `hosted.egress_refused`).

The container's environment (`choregos_core.hosted.sidecar_env`) holds the version's own variables,
then the platform's, which win: `PORT`, `CHOREGOS_RUN_ID`, `CHOREGOS_AGENT_SLUG`,
`CHOREGOS_AGENT_VERSION`, `CHOREGOS_MODEL`, the addresses above, `NO_PROXY`. It holds no credential:
not the run's key, not the run token, not an MCP server's header.

The answer: a JSON object with a `status` is the step's result, validated like `result.json`; any
other text is the summary of a step that declares no output, and `hosted_result_invalid` otherwise.
A container never ready fails the step `hosted_not_ready`; a failed response, `hosted_failed`; no
answer within `max_minutes`, `timeout`. The result reports `hosted_image`, `hosted_provenance`
(`hosted:<slug>@<version>`) and the response id. Only the project's fact commands run as checks,
and under `workspace: write` none does (see below).

**A container that works in the repository (S41-18).** `workspace` says what the container sees:

- `none` (the default): no repository; the step is not cloned.
- `read`: the runner clones, then copies the working tree into a volume the two containers share,
  mounted read-only at `/workspace` (`CHOREGOS_WORKSPACE`). The copy has no `.git` and none of the
  runner's files (`transcript.jsonl`, `result.json`, `acp-session.json`); the task is there.
- `write`: the same copy, writable. After the answer the runner brings back what changed, one file
  at a time, each judged as an ACP agent's write is (the allowed paths, the sensitive files). A
  write the guard refuses, a `.git` path, a symlink to an absolute path or out of the tree, anything
  but a regular file or symlink, a path through a symlink of the repository, a directory turned into
  a file, and under `.choregos/` anything but `.choregos/outputs/` stay in the volume, are journaled
  `hosted.write_refused` and count as permission denials. What the repository ignores
  (`__pycache__/`) is neither judged nor brought back. Deletions are mirrored under the same rules.
  The runner commits and pushes as for any agent; the clone, its `.git` and the git credential never
  leave the runner's container.

Under `write` the runner runs **no** project command (tests, lint, typecheck): they would run the
container's code inside the runner, beside the run token, the gateway key and the git credential.
The step journals `checks.skipped` and its evidence says `skipped`; the pull request's own CI judges
the change. A session (S41-11) has no repository: its pod gets `workspace: none` whatever the
version asks.

**The executor starts the container beside the runner (S41-05).** The orchestrator accepts a hosted
step when the version's image is pinned: an `image` source runs `ref@digest` (no digest,
`hosted_image_unpinned`); a `zip` source runs its build's image once the build is `ready`
(otherwise `hosted_build_not_ready`). A version that serves only `invocations_ws`
(`hosted_protocol_unsupported`) is refused, so is a stage whose actor names another backend. These
refusals are not retried and come before any gateway key. A hosted step has no skills, and a
repository only when its version asks for the workspace.

The run's pod then holds two containers:

- on Kubernetes (`k8s_job`), the agent is a **native sidecar**: an init container with
  `restartPolicy: Always` (Kubernetes 1.29 or later), started before the runner and stopped when the
  runner ends, so the Job completes. It sees the runner on `127.0.0.1` and nothing else of the run:
  plain variables only (no `envFrom`, no `valueFrom`, no volume), `enableServiceLinks: false`, the
  runner's restrictions (non-root, no privilege escalation, every capability dropped, seccomp
  `RuntimeDefault`), its own requests and limits, and the version's `termination_grace_s` as the
  pod's grace period. An image that runs as root is refused by the kubelet;
- under `local_docker`, the agent is a second container that joins the runner's network namespace
  (`--network container:<runner>`), without capabilities, removed with the runner;
- the routing executor sends a hosted step to Jobs even when it uses the pool's image; the warm pool
  (`agent_sandbox`), Tekton and Azure Container Apps refuse it before creating anything.

**The cluster must run Kubernetes 1.29 or later (S41-16).** Below 1.29 the API server drops
`restartPolicy: Always` from an init container (native sidecars are beta and on by default from
1.29): the agent becomes a plain init container that never exits, and the runner never starts. The
platform refuses it twice, with the error `hosted_unsupported_cluster`:

- **at publication**: the chart renders the cluster's version as `CHOREGOS_CLUSTER_VERSION` on the
  API (`global.kubernetesVersion`, or the version Helm or Argo CD renders for), and a hosted version
  is refused with a 422 below 1.29. Unset (`docker compose`, `make dev-up`), nothing is checked;
- **at start**: `KubernetesJobExecutor` reads the API server's `/version` (its `gitVersion`, such as
  `v1.29.3-eks-abc` or `1.30+`, then `major`/`minor`) the first time it starts a hosted step and keeps
  it for the worker's life. Below 1.29, or when the version cannot be read, it creates nothing, and
  the step fails (or the session closes `pod_failed`) without a retry, the activity's error typed
  `hosted_unsupported_cluster`. A `/version` that does not answer is retried, and not kept. A step
  without a hosted agent never reads it. `local_docker` needs no version; the executors that cannot
  hold a sidecar refuse a hosted step whatever the version.

**Under gVisor (S41-17).** A project whose policy sets `sandbox.runtime: gvisor` runs its steps,
hosted ones included, under the `gvisor` RuntimeClass: `runtimeClassName` is set on the pod, so the
runner and the agent's sidecar share one sandbox and its loopback. The project's Kyverno `Policy`
`require-gvisor` (in `runtimeclass.yaml`, which the project's `kustomization.yaml` now lists: Argo CD
applied the directory without it) refuses any pod of `proj-<slug>-runners` without it, and the Jobs
the executor creates through autogen. What it proves:

- `tests/conformance/hosted/test_hosted_under_gvisor.py` (marker `gvisor`, `CHOREGOS_TEST_RUNSC`)
  runs the eight checks with the agent inside `runsc do` (rootless, the host's network): the Python
  and Node references pass them, their log showing the sentry's kernel, and the agents that ignore
  SIGTERM or open a tunnel still fail theirs;
- `apps/orchestrator/tests/test_gvisor_policy.py` gives the real executor's hosted Job to the
  `kyverno` CLI: refused without a RuntimeClass or with `runc`, admitted with `gvisor`;
- the `hosted-gvisor` CI job runs both and fails on a skip;
- `tests/cluster/test_hosted_gvisor.py` runs S41-16's hosted Job under `gvisor` on a kind cluster
  where `make cluster-gvisor` installed `runsc` (`dev/scripts/gvisor.sh`, release and sha512 pinned).
- `tests/cluster/test_hosted_native_sidecar.py`, `test_hosted_session_pod.py` (S41-24) run the native
  sidecar, the refusal below 1.29, a session's pod with the real runner image and the shared
  workspace's `fsGroup` on kind; the `hosted-cluster` CI job runs them with the gVisor one and fails
  on a skip. They need `CHOREGOS_HOSTED_REFERENCE_IMAGE` and `CHOREGOS_HOSTED_RUNNER_IMAGE`, images
  loaded into the cluster (`tests/cluster/assets/runner.Dockerfile` is the slim runner). runsc does
  not start a pod in kind over WSL2 (cgroup v1): run the gVisor test on a cgroup v2 host.

**Five frameworks pass the conformance suite (S41-06).** [`examples/hosted/`](../examples/hosted/README.md)
holds one ticket triager on each framework, unchanged by the platform and importing nothing from
it: Microsoft Agent Framework, LangGraph, the OpenAI Agents SDK, the Claude Agent SDK and the GitHub
Copilot SDK (BYOK, no GitHub account), plus a reference on the standard library alone. Each serves
the contract with an identical copy of `responses_server.py` and writes one `answer(text)`.

`tests/conformance/hosted/` starts an agent with the environment the executor gives it, serves the
runner's real loopback listeners in front of a scripted model (chat completions, Responses and
Anthropic messages) and a scripted MCP server, sends the task the way the runner does
(`run_hosted`), and checks eight things: `readiness`, `answer`, `model_at_loopback` (the run's key
only, nothing reaches a model outside), `mcp_at_loopback` (the granted tool, with the runner's
credential), `result_valid` (built from the tool's answer), `max_turns` (one call allowed, one seen,
the step still ends), `no_egress` (no tunnel asked) and `sigterm` (exit within the grace period). An
agent that breaks the contract one way (never ready, its own model URL, no tools, telemetry left on,
SIGTERM ignored, a refused call retried forever) fails that check and only that one.

```
make conformance-hosted AGENT=langgraph PYTHON=/path/to/venv/bin/python   # as a process
make conformance-hosted IMAGE=my-registry/langgraph-triager                # as an image
```

What an image must do, learnt from the five SDKs: switch the SDK's own traffic off (tracing,
telemetry, error reports: `no_egress` fails otherwise), give the Claude and Copilot runtimes no
built-in tool, and fetch what the SDK downloads at first use (the Copilot runtime) when the image is
built. The blocking CI runs the reference as a process, and, in the `hosted-images` job (S41-23),
builds every example (the zips through the platform's own Dockerfile and `LocalDockerBuilder`, into a
registry) and runs each image through the suite; the `hosted-docker` job runs a hosted container in
the runner's workspace volume and relays a real container's `/invocations_ws`. Locally:
`CHOREGOS_TEST_HOSTED_DOCKER=1 CHOREGOS_TEST_HOSTED_REGISTRY=localhost:5055 pytest tests/conformance/hosted_real`
(with `docker run -d -p 5055:5000 registry:2`). Under Docker Desktop, `--network=host` is the VM's, so
the harness joins the agent to a socat relay container; `CHOREGOS_CONFORMANCE_DOCKER_NETWORK=relay|host`
forces the choice. `.github/workflows/hosted-agent-examples.yml` (nightly, non-blocking) is the older,
hand-written version of the same builds.

**An agent written for Foundry runs unchanged (S41-19).** `examples/hosted/agent-framework-foundry`
is the triager as a Foundry deployment holds it: Agent Framework's `FoundryChatClient` with
azure-identity's `DefaultAzureCredential`, served by Foundry's own hosting adapter
(`ResponsesHostServer`), no Choregos variable for the model. The container gets:

| Variable | Value |
| --- | --- |
| `FOUNDRY_PROJECT_ENDPOINT` | `http://127.0.0.1:4001/api/projects/choregos`, the model listener |
| `FOUNDRY_MODEL` | the step's model |
| `FOUNDRY_AGENT_NAME`, `FOUNDRY_AGENT_VERSION` | the agent's slug and version |
| `FOUNDRY_AGENT_ID` | a GUID derived from the slug, the same for every version |
| `FOUNDRY_AGENT_SESSION_ID` | the run id, or the session id in a session pod |
| `IDENTITY_ENDPOINT`, `IDENTITY_HEADER` | `http://127.0.0.1:4001/msi/token` and its header |

On the model listener:

- `…/api/projects/<name>/openai/v1/responses` and `chat/completions` are model calls like `/v1`'s:
  the run's key, the run's model, a turn each;
- `…/openai/v1/conversations` (create, read, list items, delete) is forwarded with the run's key. It
  is not a turn, so a step that has used its turns can still read its conversation;
- `GET /msi/token` with the header answers a token: the loopback key, valid for an hour. Without the
  header it answers 401, and to anything but GET 405. It is journaled `hosted.identity_token`;
- any other path of the project answers 404.

`FOUNDRY_HOSTING_ENVIRONMENT` is not set, so the hosting adapter keeps its responses in the
container. A version's `env` cannot set any `FOUNDRY_` or `AZURE_` variable, the `IDENTITY_*` pair,
`IDENTITY_SERVER_THUMBPRINT`, `IMDS_ENDPOINT` or `MSI_*`. The blocking `hosted-foundry` CI job runs
the example through the eight checks with its pinned requirements.

**A source zip is uploaded, checked and keyed for its build (S41-07).** `POST
/orgs/{org}/hosted-sources` (`application/zip`, by whoever may create an agent somewhere in the
organisation) stores the zip in the object store under its sha256; the same bytes twice are one
object. The zip must be **flat** — the agent's files at its root: a zip of the folder rather than of
its content is refused with the reason, never unwrapped — and holds at most 2,000 files and 64 MiB,
counted as it is read; a symbolic link, an absolute, `\` or `..` path, an encrypted entry or a name
twice refuses it. A version then names it:

```json
{ "type": "zip", "sha256": "sha256:…", "runtime": "python_3_13",
  "entry_point": ["python", "main.py"], "dependency_resolution": "remote_build" }
```

Where the version is written (create, publish, trial, template install), the zip is read back from
the store, refused if its bytes no longer hash to its name, and checked against the version: the
script the entry point runs (`python main.py`, `node main.js`, `./start.sh`) is in the zip
(`python -m module`, `node -e …` and a bare command such as `uvicorn` come from the dependencies; an
option's value, `node -r ./setup.js main.js`, is not taken for the script), and `remote_build` finds
the runtime's manifest at the root. `bundled` installs nothing: the zip carries its dependencies.

| Runtime | `remote_build` needs at the root | `bundled` carries |
| --- | --- | --- |
| `python_3_12`, `python_3_13`, `python_3_14` | `requirements.txt` or `pyproject.toml` | `site-packages/` (`pip install --target site-packages -r requirements.txt`), put on `PYTHONPATH` |
| `node_22` | `package.json` **and** `package-lock.json`: `npm ci --omit=dev` installs from the lock file | `node_modules/` |
| `dotnet_10` | exactly one `*.csproj`, published by `dotnet publish` in the SDK image | the output of `dotnet publish` |

The upload's `dependencies` says which manifest the zip declares (`package-lock.json` for Node, the
`*.csproj` for .NET). For .NET with `remote_build`, the assembly the entry point runs
(`dotnet Triager.dll`) comes out of the publish: it is not looked for in the zip.

The platform writes the Dockerfile itself — a `Dockerfile` in the zip is not read: `FROM` the
runtime's base image pinned by digest, the dependencies installed as root, the files in `/app`,
`USER 1000`, the port, and the entry point in exec form. Template 2 (S41-13) has no `# syntax=` line, which pulled the
floating `docker/dockerfile:1` frontend at each build (the daemon's own frontend parses it), and
passes `SOURCE_DATE_EPOCH` to the `RUN` steps of every stage. Node installs with `npm ci --omit=dev`
before the files are copied; .NET is two stages: `dotnet publish` in the SDK image (an exec-form
`RUN`, so a project name is never read by a shell), then the published output alone on the ASP.NET
runtime, which has no SDK.

| Key of `CHOREGOS_HOSTED_BASE_IMAGES` | Default, pinned by digest |
| --- | --- |
| `python_3_12`, `python_3_13`, `python_3_14` | `docker.io/library/python:3.1x-slim-bookworm` |
| `node_22` | `docker.io/library/node:22-bookworm-slim` |
| `dotnet_10` | `mcr.microsoft.com/dotnet/aspnet:10.0` (where the agent runs) |
| `dotnet_10_sdk` | `mcr.microsoft.com/dotnet/sdk:10.0` (where a `remote_build` is published) |

The operator replaces any of them with `CHOREGOS_HOSTED_BASE_IMAGES` (JSON, merged over the
defaults); a base without a digest is refused, and an empty value takes the runtime away: its
versions are then refused, saying which runtimes the platform builds (an empty `dotnet_10_sdk`
leaves `bundled` .NET zips and refuses `remote_build` ones). The version gets `build: {status:
pending, key, provenance}`, the provenance naming the base image and, for a .NET `remote_build`,
the `build_image`: the key is the hash of that Dockerfile and of the zip, so any input that changes
(the zip, the runtime, the dependency mode, the entry point, the port, the base and SDK images, the
template's version) changes it, and nothing else does; a `build` the caller writes is replaced. A
step refuses the version until a build is `ready` (`hosted_build_not_ready`).

The Node and .NET examples of [`examples/hosted/`](../examples/hosted/README.md) are zips:
`tools/hosted_dockerfile.py <dir>` zips one as an upload would, checks it as the API does and
prints the Dockerfile the platform writes for it; a test runs it on every zip example (S41-20).

**Publication builds the zip's image; a channel waits for it (S41-08).** A version is immutable,
so the build's state lives in its own row (`hosted_builds`, one per organisation and build key),
shared by every version of the same zip; the API's answers show it in `runtime.build` (`pending`,
`building`, `ready` with its `image`, or `failed` with its `error`, the end of the build's log). The
first publication of a key starts the `HostedImageBuild` workflow: it claims the build, builds on
the orchestrator's `executor` queue (the worker that holds the push credential), and records the
outcome. The image is pushed to `CHOREGOS_HOSTED_BUILD_REPOSITORY` (`global.images.buildRegistry`)
as `build-<key>` and run by its digest; without a repository the build fails, saying so.

- A version of a key already built starts nothing and moves its channels at once; a replayed build
  finds `build-<key>` in the registry and runs no builder.
- Until its build is `ready`, a version enters no channel: the moves its publication asked for are
  applied once it is built, and a release names the `hosted_build` check (409); the readiness of
  the version shows it. An older build finishing after a newer version reached production leaves
  production where it is.
- A failed build is not retried (the same inputs fail the same way). Publishing a version of the
  same zip again starts the next attempt; the previous attempt, replayed, builds nothing.
- `CHOREGOS_HOSTED_BUILDER`: `buildkit` (`buildctl` against `CHOREGOS_BUILDKIT_ADDRESS`, a rootless
  `buildkitd`) or `local_docker` (`docker build` and `docker push`, a laptop). The zip never leaves
  the platform: its archive is the build context.

**The builder (S41-13).** With `global.images.hostedBuilder: buildkit` and a `buildRegistry`, the
chart deploys `choregos-buildkitd`, a rootless BuildKit (`global.buildkitd`: its image pinned by
digest, its port, its resources), and points the workers at it; `global.images.buildkitAddress`
names a daemon of the operator's instead, and the chart then deploys none. The orchestrator image
carries `buildctl`, copied from the very image the chart deploys.

- **Same zip, same digest.** `buildctl` sets `SOURCE_DATE_EPOCH=0` and pushes with
  `rewrite-timestamp=true`: every file and the image's dates are clamped to the epoch, so a zip
  built again from an empty cache gives the image it gave the first time. Without
  it, "already built" would only mean "built once on this daemon".
- **Push credential**: `global.buildkitd.pushSecret`, a `kubernetes.io/dockerconfigjson` Secret
  mounted in the builder only; the worker that sends the build holds none.
- **Mutual TLS**: `global.buildkitd.tls.serverSecret` and `clientSecret`, two `kubernetes.io/tls`
  Secrets carrying `ca.crt` (cert-manager writes them so). The worker then gets
  `CHOREGOS_BUILDKIT_TLS_DIR` and passes `--tlscacert`, `--tlscert`, `--tlskey`; without them the
  gRPC is plain and the NetworkPolicy alone keeps other pods off it.
- **Network**: the builder admits the orchestrator alone, on its port, and reaches DNS and the
  Internet's `egressPorts` (443, 80), never a private range; a registry inside the network is named
  by `registryCidrs` and `registryPorts`.
- **Its weaker profile** (seccomp and AppArmor `Unconfined`, privilege escalation, SETUID/SETGID) and
  the Kyverno exception that names it are in [security](security.md#the-builder-of-hosted-zips).

The `hosted-build` CI job runs the chart's daemon image with the chart's profile, builds the
reference zip into a real registry, finds it again under its key, rebuilds it after `buildctl prune
--all` to the same digest, and runs the image through `make conformance-hosted`.

Not yet: a trial of a zip version runs only once a published version of the same zip is built
(`hosted_build_not_ready` before); the first zip version of an agent leaves it without a production
version until its image is built.

**A person talks to a prompt agent directly (S41-09).** `POST /orgs/{org}/agents/{slug}/responses`
takes the Responses API's shape (`input` as a string or user messages, `stream`) plus the `project`
the session works in and an optional `max_usd`. Without `session_id` it opens a session; with it,
the next turn. The answer is a `response` object (`status`, `output`, `output_text`, `usage`, and an
`error` with its `code` when it failed); `GET` and `DELETE /orgs/{org}/agent-sessions/{id}` read
the session with its transcript and close it.

- **Who**: the caller needs `item.control` in the project (a developer or above), and the agent must
  work there: an organisation's agent, or that project's own. A personal agent works in no project
  (422). A session is its opener's: anyone else is answered 404.
- **What it runs**: the version the project is served (its pin, channel and overrides), its
  instructions verbatim as the system message (no `{{ ticket.* }}` rendering: there is no work
  item), its model, and the prompt loop of a workflow step. Only a `prompt` agent answers here; a
  voice agent answers on `/realtime` (below), a hosted one in its session's pod (S41-11, below).
- **Budget**: the session reserves the smallest of what is left under its `max_usd` (1 USD by
  default), the agent's run, day and month budgets, the project's day and the organisation's month,
  and mints **one** gateway key at that amount for an hour, sealed in the secret store and never
  returned. The key's row in `gateway_keys` (`session:<id>`) counts as an open reservation while
  the session lives. A turn the gateway refuses for budget fails with `budget_exceeded` and closes
  the session; another model error fails the turn and leaves the session open.
- **Closing** (`DELETE`, the hour elapsed, or a spent key) reads the key's spend, books it in the
  ledger with `session_id` and the agent's version, revokes the key and forgets its secret. Closing
  twice books once. An expired session is closed by the next session opened in its project.
- **Tools**: the reads the agent's `mcp_servers` grant in the project, through the courtier as a
  run's (the policy's call cap, the schema, the injection guard of `sandbox.prompt_injection`, a
  ledger line per call). A write or an approval tool is not offered: it needs a work item.
- **Transcript**: `agent_session_events`, numbered, text only: inputs, the loop's turns and tool
  calls, each response, opening and closing.
- With `stream`, the turn is computed first and sent as server-sent events `response.created`,
  `response.output_text.delta` and `response.completed` (or `response.failed`): the text arrives in
  one delta, not token by token.

**A person speaks to a voice agent (S41-10).** `/orgs/{org}/agents/{slug}/realtime?project=…`
(optional `max_usd`) is a WebSocket speaking the OpenAI Realtime events; the API relays it to the
gateway's realtime socket (`ws(s)://<gateway>/realtime?model=…`), with the session's key.

- **Before the socket is accepted**: a browser's `Origin` must be the console's, the API's or one of
  `CHOREGOS_CORS_ORIGINS` (no `Origin` — a CLI, a server — passes); the caller signs in with the session
  cookie or an API token and needs `item.control`, as for `/responses`; the organisation's gateway
  must declare the `realtime` capability; the session is opened (one key, the same reservation) and
  the upstream socket connected. Any refusal closes with 1008 and its reason, and no key outlives it.
- **The agent owns the session**: the relay sends one `session.update` (the agent's instructions,
  voice, PCM16 24 kHz audio, turn detection, input transcription with
  `CHOREGOS_REALTIME_TRANSCRIPTION_MODEL`, the grant's read tools), then the greeting. A caller's
  `session.update`, a `response.create` carrying its own `response`, a `conversation.item.create`
  that is not its own `input_text`/`input_audio` message (a `function_call_output` would forge a
  tool's answer) and any other event outside the audio buffer, cancel, truncate and delete events
  are answered with an `error` event and never reach the model.
- **Tools**: a function call of the model runs through the courtier like a prompt session's, only
  if the grant offers it; anything else is journaled `tool.refused` and answered "not granted". The
  output goes back to the model, then a `response.create`.
- **Limits**: a caller's frame over 1 MiB closes with 1009; `max_session_minutes` closes with 1000;
  a gateway error in budget words closes with 1008. Each way, the session closes as a prompt one:
  the key's spend booked with `session_id`, the key revoked.
- **Transcript**: the caller's and the agent's transcripts and texts, tool calls and refusals, each
  `response.done` with its usage. Audio is relayed and never kept.

**What a voice session spends (S41-15)** is measured by the relay, not read from the gateway, which
prices a realtime socket late or not at all, while an audio token costs several times a text one.

- **Prices**: when the session opens, the API takes the model's prices from the organisation's
  override (`input_per_1k_override`/`output_per_1k_override`, then applied to every token) or else
  from the gateway: text in and out, audio in and out (`input_cost_per_audio_token`,
  `output_cost_per_audio_token` in LiteLLM), cached input (`cache_read_input_token_cost`, else the
  text price). A model with neither override nor the four gateway prices opens no session (422,
  "no audio price"): an unpriced token is a spend no cap can stop. The prices are journaled in
  `session.opened`.
- **Each `response.done`** is priced from its `usage`: `input_token_details` (text, audio, cached
  with its own split) and `output_token_details`. A total without its split is priced at the dearer
  kind, a cached total without its split is taken from text first; the model is never measured
  cheaper than it may cost. The event is journaled with `priced_by: relay`, its cost and tokens, the
  sum so far and the cap.
- **The cap** is the session's reservation (`max_usd` and the project's budgets, as for a prompt
  session) and, when the catalogue entry sets `max_usd_per_minute` (`PATCH /orgs/{org}/models/{id}`,
  `clear_max_usd_per_minute` to remove it), that rate for each minute begun, the first one whole.
  Once the sum reaches it, the caller first receives that `response.done`, then the socket closes
  with 1008 "the session spent X of its Y USD".
- **Booking**: the ledger books the relay's sum with `session_id`; the gateway's count on the key is
  kept in `session.closed` as `gateway_cost_usd`, for comparison only. A voice session that never
  answered books what the gateway counted, as before.
- **Live check**: `tests/live/test_realtime_live.py` (`make test-live`, with
  `CHOREGOS_LIVE_LITELLM_URL`, `CHOREGOS_LIVE_LITELLM_KEY` and `CHOREGOS_LIVE_REALTIME_MODEL`) opens a
  real realtime socket through the gateway and prices its `response.done`.

**A person talks to a hosted agent directly (S41-11).** The same `POST /orgs/{org}/agents/{slug}/responses`,
the same rights, budget, key, closing and transcript as a prompt session; what answers is the
agent's own container, in a pod of its own.

- **One pod per session**, started by its first response: the workflow `AgentSessionPod` (id
  `agent-session-{id}`, so a second start is refused by Temporal) asks the executor, on its own
  queue, for the pod a step gets — the runner beside the pinned image as a native sidecar — with the
  runner in `serve-session` mode. A version no pod could run (an unpinned image, a zip not built) is
  refused without a retry, and the session closes `pod_failed`.
- **Its Secret carries the session's key and a token whose subject is `session-{id}`**; neither is in
  the stage input the workflow writes (`agent_sessions.stage_input`), nor in a Temporal payload, nor
  in the container's environment. That token reads `/internal/agent-sessions/{id}` only: a run's
  token, or another session's, is refused (403).
- **A turn**: the response journals `turn.queued` and waits for the pod's answer (the agent's
  `readiness_timeout_s` plus 300 s at most, then `hosted_timeout`). The runner takes it from
  `turns/next` (a long poll that holds no transaction), sends it to the container on `POST
  /responses` or `/invocations`, whichever the agent serves, and posts how it answered; a second post
  of the same turn changes nothing. A turn the container could not answer fails the response with
  `hosted_failed`, `hosted_not_ready`, `hosted_timeout`, `max_turns` (the agent's `limits.max_turns`
  model calls **per turn**, 20 by default) or `budget_exceeded`, which also closes the session.
- **The walls are a step's**: the model through the loopback with the session's key, the CONNECT
  proxy with the session's network, and the session's tools served by the runner itself on
  `/mcp/choregos` (`CHOREGOS_MCP_SERVERS=choregos`): each `tools/call` is judged by the guard rails,
  then sent through the internal API to the courtier as a prompt session's. No other MCP server is
  served. What the runner saw — refused model calls, tool calls — is journaled with the turn.
- **The pod stops** at the first of: the session closed (`DELETE`, a spent key), `CHOREGOS_HOSTED_SESSION_IDLE_S`
  without a response (600 s by default; each response restarts the wait), the session's expiry.
  Stopping fails the turns still waiting, cancels the pod and closes the session with that reason;
  replayed, it changes nothing.
- An agent that serves only `invocations_ws` is answered 422 here: its session is on `/realtime`
  (below).

**A person on a hosted agent's own socket (S41-21).** A hosted agent that declares `invocations_ws`
answers on the same `/orgs/{org}/agents/{slug}/realtime?project=…` as a voice agent, with the same
door (`Origin`, rights, the session opened before the socket is accepted). The API never connects
to the pod: each text frame the caller sends is journaled (`socket.in`); the pod's runner reads
them (`GET /internal/agent-sessions/{id}/socket/next`, a long poll), connects the container's
`ws://127.0.0.1:$PORT/invocations_ws` at the first one, writes each frame to it, and posts what the
container sends back numbered from 1 (`POST …/socket/out`). Each number is journaled once, so a post
sent again changes nothing, and one that would leave a hole is refused (409). The caller's handler
reads the journal and sends each frame once, in order, unmodified.

- **Frames**: text only, 1 MiB at most; a binary frame closes with 1003, a larger one with 1009.
  Each frame the caller sends is a turn for `max_turns`, as a prompt turn is.
- **How it ends**: the caller's close closes the session and its pod; the container's close
  closes the caller's socket with its code (one a server may not send becomes 1011) and the
  session, `agent_closed`; a spent key closes with 1008 `budget_exceeded`; a container never ready,
  or whose socket refuses the connection, closes with 1011.

**A socket that drops (S41-21).** A socket's first event is the platform's,
`{"type": "choregos.session", "session_id": …}`. Every voice event after it carries `seq`, its
number in the session. A socket that goes away without a close frame (1006) does not end the session:
for 30 s the model's socket (voice) or the pod (hosted) stays, its events priced and kept. The
caller that comes back on the same URL with `session=<id>&after=<n>` — the last voice `seq` it read,
or the number of the container's frames it read — is checked again (the rights, and the session
must be its own, of this agent, in this project), then gets what it missed, then the live events.
One key, one pod, each response priced once.

- **Past the grace** the session closes `caller_gone`, its key revoked; a late resume gets 1008.
  A hang-up (a close frame) still closes at once.
- **Two sockets**: a newer one takes the session; the older one is closed with 1000.
- **What is held**: a voice session holds the last 8 MiB of its events in the API process that
  opened it. A resume after an event no longer held is refused (1008), never skipped; a resume on
  another API replica is answered 1013 (try again) — sticky sessions keep it on one. A hosted
  session is read from the journal, on any replica.
- **Limits**: a hosted session's caller reads the journal every 0.2 s while attached; the
  container's frames are journaled as text. A pod that restarts replays the caller's frames to the
  new container, which starts its own conversation again.

### Signatures, by cosign (S41-14)

Publication asks the `cosign` binary the API image carries (v3.1.3) about the **pinned** reference,
`ref@sha256:…`, never a tag: `cosign verify --output json`, by the operator's signer.

- **By key**: `global.images.hostedCosignKeySecret` names a Secret of the release's namespace holding
  the public key under `cosign.pub`. The chart mounts it on the API
  (`CHOREGOS_HOSTED_COSIGN_KEY=/etc/choregos/cosign/cosign.pub`; the variable also takes a KMS URI
  cosign reads).
- **Keyless**: `hostedCosignIdentity` and `hostedCosignIssuer` (`CHOREGOS_HOSTED_COSIGN_IDENTITY`,
  `_ISSUER`), the certificate's subject and its OIDC issuer as regular expressions. Cosign searches
  them **anchored** (`^(?:…)$`: cosign searches, so a bare `a@b` would accept `evil-a@b.evil`) and,
  when cosign reports the certificate (a legacy signature), the API matches its subject and issuer
  **whole** (`re.fullmatch`) again. A cosign 3 bundle reports none in `--output json`: there cosign's
  own exit code, with the anchored expressions, is the verdict (proved against the real Sigstore,
  S41-25). `https://github\.com/acme/.+` does not accept `https://evil.example/?https://github.com/acme/x`.
  Anchor them anyway (`^…$`): Kyverno searches them too.
- `hostedCosignTlog: false` (`CHOREGOS_HOSTED_COSIGN_TLOG`) skips the transparency log, for a key
  whose signatures were never uploaded to Rekor.

A signature counts only when cosign accepts it **and** its payload names the pinned digest: a
signature copied onto other bytes, or one of another image, does not vouch for this one. A key and
an identity together, an identity without its issuer, an expression that does not compile: nothing
verifies, and `enforce` refuses every image with that reason. Every failure is a verdict, shown to
the publisher: cosign missing, 60 s without an answer, cosign's own `Error:` line.

**At admission.** `hostedCosignPolicy: true` renders the Kyverno ClusterPolicy
`choregos-hosted-images` (Kyverno required). In the release's namespace, `proj-*` and `choregos-*`,
an image under `hostedRegistries` in `containers`, `initContainers` (a native sidecar is one) or
`ephemeralContainers` runs only pinned by `@sha256:`; under `enforce`, Kyverno also verifies it
against the same key Secret or keyless identity. The chart refuses to render the policy without
`hostedRegistries`, or under `enforce` without a signer. Kyverno reads the key Secret as its
admission controller's ServiceAccount: the chart renders a `get` Role on that one Secret for
`kyverno:kyverno-admission-controller` (`hostedCosignKyvernoGrant: false` leaves it to the platform
team; `hostedCosignKyverno` names another ServiceAccount). Kyverno must also be able to reach the
registry (an HTTP one needs `--allowInsecureRegistry`).

**Signing for admission.** Kyverno reads one signature format per policy, so
`hostedCosignFormat` names it: `bundle` (the default, cosign 3's own: `cosign sign` writes a Sigstore
bundle as an OCI referrer, or under its fallback tag on a registry without the referrers API) or
`legacy` (the `.sig` tag of `cosign sign --new-bundle-format=false`). Under `bundle` the policy's
`verifyImages` entry has `type: SigstoreBundle`, which finds no `.sig`; under `legacy` it keeps
Kyverno's default `Cosign` type, which finds no signature in a bundle. An image signed only in the
other format is refused at admission; sign in both to serve either. The API's own check (cosign 3)
reads both formats. Proved in a cluster for both values by `tests/cluster/test_hosted_images_admission.py`
(job `hosted-admission`, Kyverno 1.19.1).

What is not covered: the cosign subprocess does not go through the platform's egress client; the
registry was judged by the organisation's egress allowlist when the digest was resolved, and keyless
verification also reaches Sigstore (`tuf-repo-cdn.sigstore.dev`, Rekor), which the API's network
must open. A private registry needs credentials cosign does not have yet. A zip version's built
image is not signed by the platform: the policy requires its digest, and under `enforce` with
`buildRegistry` under `hostedRegistries` Kyverno would refuse it, so keep the build repository out
of the hosted prefixes.

### In the console (S41-12)

**New agent** offers three more starts beside the blank form and the remote agent:
`/agents/new/runtime?kind=prompt|voice|hosted`.

- **Prompt agent**: name, identifier, instructions, an optional model, how it answers (text or a
  JSON object) and how it uses tools. Tools and knowledge are added on the agent's page afterwards.
- **Voice agent**: the same, with a realtime model required, its voice, its greeting, who detects
  the end of a turn, and the longest call (120 minutes at most).
- **Hosted agent**: an image reference (its digest is resolved at publication), or a zip uploaded
  to `POST /orgs/{org}/hosted-sources` and summarised (hash, files, where the dependencies are
  declared; a zip that declares none can only be `bundled`), its runtime (Python 3.12 to 3.14,
  Node 22, .NET 10; the suggested entry point follows the runtime until it is written over, and a
  runtime the operator took away is refused by the API, on the page), its entry point, the protocols it serves (`responses`, `invocations`), its
  port (4001 to 4003 are refused: they are the runner's) and how long it may take to be ready.

The form sends nothing the API would refuse for a missing field, and says what is still needed.
**Publish** and **Save as draft** create the agent; a refusal from the API stays on the page. On
the agent's page, **Model and runtime** shows the runtime of the version, the digest an image runs
at, and the build of a zip: pending, building, built (with its image) or failed (with its error).
While the latest version's build is pending or building, the page asks again every 3 seconds, and
stops once it has ended.

The rest of the stream — signatures, live voice, the cluster proofs — is planned in
[`docs/plan/`](plan/STATUS.md) as S41-14 to S41-20.
