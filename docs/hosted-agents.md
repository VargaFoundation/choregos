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
- a step whose agent has one of the three runtimes stops before any key is minted, with the reason
  (`AgentIndisponible`, not retried); voice agents are never steps.

What the validation already refuses: a `backend` on any of the three, `skills` on voice and hosted,
a voice agent without a model, a hosted port on the runner's loopback listeners (4001-4003), a hosted
`env` naming a reserved variable (`PORT`, the proxy variables, anything under `CHOREGOS_`,
`FOUNDRY_`, `OPENAI_`, `ANTHROPIC_`, `AZURE_`), a protocol listed twice, a zip runtime the platform
has no base image for.

The rest of the stream — the runner's loopback proxies, the sidecar in the run's pod, the builds,
the sessions and the console — is planned in [`docs/plan/`](plan/STATUS.md) as S41-03 to S41-21.
