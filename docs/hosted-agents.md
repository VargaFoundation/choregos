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

The rest of the stream — the runner's loopback proxies, the sidecar in the run's pod, the builds,
the sessions and the console — is planned in [`docs/plan/`](plan/STATUS.md) as S41-04 to S41-21.
