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
- a hosted **zip** source is refused: the source upload (S41-07) and the build (S41-08) come first;
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
  (`choregos-loopback`) is accepted. Every call is a turn: past `max_turns` the call is answered 429
  `max_turns` and never forwarded (`hosted.model_refused`). A gateway budget refusal ends the step on
  `budget`;
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

The executor does not start the container yet: the sidecar in the run's pod is S41-05, and until then
the orchestrator still refuses a hosted step. The rest of the stream — the builds, the sessions and
the console — is planned in [`docs/plan/`](plan/STATUS.md) as S41-05 to S41-21.
