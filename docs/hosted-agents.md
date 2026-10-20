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
  and logs `hosted.signature_unverified`, `off` (the default) does not ask. No real verifier is wired
  yet (S41-14): under `enforce` every image is refused outside the fakes;
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
(`hosted:<slug>@<version>`) and the response id. Only the project's fact commands run as checks: the
container does not work in the repository yet (`workspace: write` is S41-18).

**The executor starts the container beside the runner (S41-05).** The orchestrator accepts a hosted
step when the version's image is pinned: an `image` source runs `ref@digest` (no digest,
`hosted_image_unpinned`); a `zip` source runs its build's image once the build is `ready`
(otherwise `hosted_build_not_ready`). A version that serves only `invocations_ws`
(`hosted_protocol_unsupported`) or asks for a workspace (`hosted_workspace_unsupported`, until
S41-18) is refused, so is a stage whose actor names another backend. These refusals are not retried
and come before any gateway key. A hosted step has no repository and no skills.

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
built. The blocking CI runs the reference; `.github/workflows/hosted-agent-examples.yml` builds the
six images and runs the suite against each, nightly, without blocking.

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
script the entry point runs (`python main.py`, `./start.sh`) is in the zip (`python -m module` and a
bare command such as `uvicorn` come from the dependencies), and `remote_build` finds a
`requirements.txt` or a `pyproject.toml` at the root. `bundled` installs nothing: the zip carries its
dependencies under `site-packages/` (`pip install --target site-packages -r requirements.txt`), which
is put on `PYTHONPATH`.

The platform writes the Dockerfile itself — a `Dockerfile` in the zip is not read: `FROM` the
runtime's base image pinned by digest (`python:3.12-slim-bookworm` and `python:3.13-slim-bookworm`
from Docker Hub; the operator replaces them with `CHOREGOS_HOSTED_BASE_IMAGES`, and a base without a
digest is refused), the dependencies installed as root, the files in `/app`, `USER 1000`, the port,
and the entry point in exec form. The version gets `build: {status: pending, key, provenance}`: the
key is the hash of that Dockerfile and of the zip, so any input that changes (the zip, the runtime,
the dependency mode, the entry point, the port, the base image, the template's version) changes it,
and nothing else does; a `build` the caller writes is replaced. A step refuses the version until a
build is `ready` (`hosted_build_not_ready`). Node, .NET and Python 3.14 have no base image yet
(S41-20): such a version is refused.

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
  `buildkitd`) or `local_docker` (`docker build` and `docker push`, a laptop). The chart passes
  `global.images.hostedBuilder` and `buildkitAddress` to the workers but deploys no `buildkitd` yet
  (S41-13); the zip never leaves the platform, its archive is the build context.

Not yet: a trial of a zip version runs only once a published version of the same zip is built
(`hosted_build_not_ready` before); the first zip version of an agent leaves it without a production
version until its image is built.

The rest of the stream — the sessions, the console and the real builder — is planned in
[`docs/plan/`](plan/STATUS.md) as S41-09 to S41-21.
