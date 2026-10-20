# 0080 — Hosted, prompt and voice agents

- **Status**: accepted, 2026-10-20
- **Concerns**: the agent registry (`AgentSpec.runtime`: `prompt`, `voice`, `hosted`), the stage
  input (`StageInput.hosted`, `StageInput.prompt`), the runner (loopback proxies, the hosted and
  prompt steps), the executors (a second container in the run's pod), the image supply chain
  (registry allowlist, digest pinning, signatures, builds from a source zip), direct invocation
  (agent sessions, the Responses API, realtime WebSockets); builds on
  [ADR 0033](0033-agent-registry-and-skill-library.md), [ADR 0062](0062-remote-agents-over-a2a.md),
  [ADR 0063](0063-agents-written-in-code.md), [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md)
  and [ADR 0071](0071-an-agent-version-reaches-production-through-channels.md)

## Context

A team that already has an agent — written with Microsoft Agent Framework, LangGraph, the OpenAI
Agents SDK, the Claude Agent SDK, the GitHub Copilot SDK or plain code — wants to run it on the
platform **as it is**, under the platform's guarantees. A code agent (ADR 0063) does not allow that:
its image must be built from the runner image, it must speak ACP through `choregos-agent-sdk`, and
only the operator names images.

Azure AI Foundry's Agent Service sets the vocabulary the market now uses, with three kinds:

| Foundry kind | What it is |
| :-- | :-- |
| prompt | instructions, a model and tools; no code, no container |
| voice-based prompt | the same, spoken: a realtime model over a WebSocket, PCM16 audio |
| hosted | your own code in a container (or a source zip the platform builds) that serves a small HTTP contract: `GET /readiness`, `POST /responses` and/or `POST /invocations`, optionally `GET /invocations_ws`, on port 8088 |

A container written for Foundry's hosted contract imports nothing from Foundry: the framework serves
the OpenAI Responses protocol and reads its model endpoint from the environment. If the platform
serves the same contract, the same image runs on both.

Three questions had to be answered: where the governance goes when the agent's code is not ours,
where an image built from a zip comes from, and how an agent is called outside a workflow (a voice
agent cannot be a workflow step).

## Decision

### The kinds

1. **`AgentCreate.kind` is unchanged.** It says who runs the agent (`internal`, `external`,
   `remote`); the platform runs all three new shapes, so they are `internal`. They are three new
   values of the `AgentSpec.runtime` union that ADR 0063 opened:
   - **`prompt`**: `{kind: prompt, max_output_tokens?, response_format?, tool_choice}`. It uses the
     spec's `instructions`, `model`, `mcp_servers` and `knowledge`. `backend` must be empty: no
     coding agent, no container code. The step runs a tool loop inside the runner; a session runs the
     same loop inside the API.
   - **`voice`**: `{kind: voice, voice, audio {format: pcm16, sample_rate: 24000, channels: 1},
     greeting?, turn_detection: server_vad|none, max_session_minutes}`. `model` must be a catalogue
     model marked `realtime`; `backend` and `skills` must be empty. A voice agent only runs as a
     session: a workflow actor naming one is refused at publication of the workflow.
   - **`hosted`**: `{kind: hosted, source, protocols, port, readiness_timeout_s, termination_grace_s,
     workspace, api_format}`. `source` is `{type: image, ref}` or `{type: zip, sha256, runtime,
     entry_point, dependency_resolution}`. `protocols` is a non-empty subset of
     `responses | invocations | invocations_ws`. `backend` and `skills` must be empty (nothing in the
     container reads a `SKILL.md`).
2. **What the API fills, never the caller**: `source.digest` (the image digest the publication
   resolved, like `A2aRuntime.pinned_card`) and `build` (for a zip source: the build's status,
   output digest and provenance).

### The hosted contract

3. **The contract is Foundry's**, so that an image built for Foundry runs here unchanged: plain
   HTTP/1.1 on `$PORT` (8088 by default, bound to `0.0.0.0`), `GET /readiness` answering 200 when
   the agent can take a request, `POST /responses` (the OpenAI Responses API, streaming by SSE when
   asked) and/or `POST /invocations` (an opaque JSON body), optionally `GET /invocations_ws` (a
   WebSocket, frames of at most 1 MB), a clean exit on SIGTERM within the grace period. Nothing from
   Choregos is imported in the container.
4. **The pod**: the runner is the main container; the hosted image is a *native sidecar* (an init
   container with `restartPolicy: Always`, Kubernetes ≥ 1.29) in the same pod and network namespace.
   The runner polls `/readiness` itself (a `startupProbe` would hold the runner back), sends the
   step, reads the answer, writes the result. When the runner exits the Job completes and the
   kubelet sends SIGTERM to the sidecar; cancelling the run deletes the Job, which does the same.
   `terminationGracePeriodSeconds` is `termination_grace_s`.
5. **Governance moves to the boundary.** The runner serves three loopback listeners the container
   is pointed at, and holds every credential:
   - **model** (`127.0.0.1:4001`): forwards to the gateway with the run's key, counts every model
     request as a turn and refuses at `max_turns` before the request leaves the pod, journals each
     call; streaming passes through;
   - **MCP** (`127.0.0.1:4002/mcp`): every `tools/call` goes through the runner's guard and the turn
     guard before it is relayed to the run's MCP servers (`choregos-tools`, connectors through the
     broker), so the tool policy and the human gates still apply;
   - **egress** (`127.0.0.1:4003`): an HTTP `CONNECT` forwarder that adds the run's identity for the
     per-run egress proxy (ADR 0070) — or, without one, refuses every host outside the run's network.

   **No secret enters the user's container**: the run token, the run key, the remote token and git
   credentials stay in the runner. The network policy that blocks a pod's direct egress covers both
   containers, since they share one network namespace.
6. **The environment the container receives**: `PORT`; `CHOREGOS_RUN_ID`, `CHOREGOS_AGENT_SLUG`,
   `CHOREGOS_AGENT_VERSION`, `CHOREGOS_MODEL` (the model the framework should ask for),
   `CHOREGOS_MCP_URL`; `OPENAI_BASE_URL=http://127.0.0.1:4001/v1`, `ANTHROPIC_BASE_URL=http://127.0.0.1:4001`
   and both API keys set to the constant `choregos-loopback` (the loopback ignores them; SDKs refuse
   to start without one); `HTTP_PROXY`, `HTTPS_PROXY` (`http://127.0.0.1:4003`) and `NO_PROXY`
   (`127.0.0.1,localhost`). The `FOUNDRY_*` variables and a loopback managed-identity endpoint are
   added so that code written against `azure-ai-projects` runs unchanged (S41-19); the prefix is
   reserved for that.
7. **A hosted step** sends one Responses request: the instructions as `instructions`, the rendered
   task as `input`, the run and transition ids in `metadata`. A final text that parses as a JSON
   object with the transition's outputs fills `outputs`; otherwise it is the `summary`. Files the
   container writes under `/outputs` become the step's artifacts. With `workspace: none` (the
   default) the repository is not mounted; `read` mounts it read-only, `write` lets the container
   change it and the runner commits and pushes under the same path rules as any agent (S41-18): the
   container never holds a git credential.

### The supply chain

8. **An image a person names** must be under a prefix of the operator's allowlist
   (`CHOREGOS_HOSTED_REGISTRIES`, chart `global.images.hostedRegistries`); empty, no image reference
   is accepted and only zip sources are. Publication resolves the reference to a digest and pins it:
   runs pull `ref@sha256:…`, never a tag. With `CHOREGOS_HOSTED_COSIGN=enforce` the publication
   refuses an image whose signature does not verify against the operator's identities; `warn`
   records the failure; `off` skips it.
9. **A zip source is built at publication, never at run start** — ADR 0063's refusal of installing
   code when a run starts stands. The zip is flat, at most 64 MiB and 2,000 entries, refused on a
   symlink, an absolute or `..` path, or a missing entry point; it is stored by its sha256. The
   platform writes the Dockerfile itself, from a platform base image pinned by digest
   (`python_3_12`, `python_3_13`, `python_3_14`, `node_22`, `dotnet_10`). `remote_build` installs the
   declared dependencies (`requirements.txt`, `package-lock.json`, `*.csproj`) through the build's
   egress; `bundled` installs nothing. The build key is the hash of the zip, the runtime, the
   dependency mode, the base image digest and the template version: the same inputs never build
   twice. The build's provenance (those inputs, the builder and its version, the output digest) is
   stored with the version. A version whose build is not `ready` cannot reach a release channel.

### Direct invocation

10. **A session is a new object beside runs**: a run belongs to a work item, a session to a person
    calling an agent. `POST /agents/{slug}/responses` (the Responses API, SSE when `stream` is true)
    serves prompt and hosted agents; `GET /agents/{slug}/realtime` (a WebSocket speaking the OpenAI
    Realtime events) serves voice agents and hosted agents declaring `invocations_ws`. Each session
    has its own gateway key with its own budget, booked against the project; the tools it reaches are
    the agent's grant **intersected** with the caller's rights; every event is journaled; a
    transcript is kept, audio never is. The client of a voice session cannot change the session's
    configuration (`session.update` is answered with an error and dropped): the agent owns it.

## Consequences

- A contract change: three runtime schemas, `StageInput.hosted` and `StageInput.prompt`, the source
  upload, the session and realtime endpoints. Old stage inputs and agent versions validate as before
  and keep their digests.
- The runner grows a second way to govern an agent: at the boundary (requests counted and journaled
  at the loopback) instead of inside the ACP session. It is weaker on one point, stated here: a
  hosted agent's *own* actions inside its container (files it writes, processes it starts) are not
  permission-gated; they are bounded by the container's sandbox, its network and the absence of any
  credential.
- `agent_sandbox`, `aca` and `tekton` refuse a hosted step until they can run a second container;
  `k8s_job` and `local_docker` run it, and the routing executor sends hosted steps to the custom-image
  executor.
- A deployment needs a registry the builder can push to, and Kubernetes 1.29 for native sidecars;
  without either, hosted agents are refused with an explicit reason, not run in a degraded way.

## Alternatives discarded

- **A new `kind` per shape**: `kind` already means who runs the agent; a second meaning would make
  `remote` + `hosted` combinations meaningless.
- **Our own hosted contract, or ACP over HTTP**: a container would have to be written for Choregos;
  the point is that it is not.
- **The runner inside the user's image** (as for code agents): forces `FROM` our image and leaks the
  run's credentials into code we do not control.
- **Installing the zip's dependencies at run start**: refused by ADR 0063 for its supply chain.
- **Trusting the gateway's realtime spend**: realtime usage arrives per response; the relay books it
  from `response.done` and enforces a per-minute ceiling as a backstop (S41-15).
