# Where Choregos sits

An honest comparison, written on 2026-09-25 from public sources. It says what others do
better; that is the part worth reading.

## In one sentence

Choregos is the **delivery layer**: it turns a ticket into a controlled change — stages,
roles, gates that refuse, evidence the platform measured, a cost ledger, a release train —
on top of whatever *runtime* runs the agent. It is not a coding agent, and it is not a
sandbox runtime.

## Against the runtimes

| | google/ax | kubernetes-sigs/agent-sandbox | Choregos |
| :-- | :-- | :-- | :-- |
| What it is | Task / Workspace / Gateway / Model primitives on GKE | `Sandbox`, warm pools, suspend/resume, `RuntimeClass` | stages, gates, evidence, ledger, trains |
| BYO agent | image | image | any ACP agent (ADR 0002) |
| Egress control | allowlist gateway | none (bring your own) | per-project Squid proxy from the policy, agent-side guardrails first |
| Credentials | per task | per sandbox | one capped virtual key per run; tool keys never reach the agent (ADR 0014) |
| Suspend / snapshot | yes | yes | **no** — ADR 0016 proposes to sit on agent-sandbox for it |
| Kernel isolation | gVisor | `RuntimeClass` | gVisor only where the policy asks; no seccomp profile of ours yet |
| Stages, gates, evidence, cost, trains | no | no | yes |
| Control plane | cluster-scoped, unsupported by vendor | cluster-scoped, v1beta1 | namespaced; a tenant can install it |

Reading: ax and agent-sandbox solve problems Choregos has (density, suspend), and Choregos
solves problems they do not have (what is a stage, who signed off, what did it cost). The
`agent_sandbox` executor (ADR 0016) is the proof of that layering, when it ships.

## Against the agent platforms

| | OpenHands | Tembo | Copilot / Codex / Devin / Factory / Cursor | Choregos |
| :-- | :-- | :-- | :-- | :-- |
| Agent | its own | several harnesses | locked | any ACP agent |
| Trigger | chat, issue | ticket | chat, issue | ticket, with a workflow per ticket |
| Budgets | per project, built-in gateway | — | credits | per stage, hard cap on the key, tool calls in the same ledger |
| Prompt-injection detection | **yes, with automatic halt** | — | varies | **no** (P2 item; the guardrails are a net, not a wall) |
| Gates with refusal semantics | no | no | no | yes, statically validated (ADR 0010) |
| Evidence measured by the platform | partial | no | no | tests run by the runner, business facts, gate verdicts |
| Human-in-the-loop | chat | — | chat | a state with an SLA and an escalation; no resume of the agent's context across the wait |
| Self-hosted, GitOps-only | yes | SaaS | mostly no | yes, Apache 2.0 |
| Non-code work | no | no | no | yes (ADR 0012) |

Reading: OpenHands' injection detection and the mature HITL chat of the products are ahead.
Choregos' answer to injection today is mechanical (scope refused, diff reverted, secrets
gated), not detective.

## Against the tool brokers

| | treg | Obot / Docker MCP Gateway | Choregos catalogue (ADR 0014) |
| :-- | :-- | :-- | :-- |
| Breadth | ~2,900 endpoints | MCP servers | what the deployment declares — a few tools |
| Credentials | server-side, one token per member | brokered | server-side, the agent holds only its run token |
| Real tool name and URL hidden from the agent | no | no | yes |
| Auto-discovery of tools | yes | yes | refused: 404, not 403 |
| Per-group access | — | partial | yes, two locks (deployment and project) |
| Cost per call in the same ledger as models | no | no | yes |

Reading: breadth is theirs. A federated catalogue (P3) would take entries *from* them, into
a PR a human merges.

## Standards

- **ACP**: adopted (ADR 0002); more than fifty agents speak it.
- **MCP 2026-07-28**: the sidecar predates it (stateless core, `tasks`, elicitation,
  OAuth resource servers); conformance is a P3 item.
- **A2A**: no surface. Choregos orchestrates agents through stages, not through agent-to-agent
  messages; nothing prevents a stage from being an A2A client later.
- **OpenTelemetry GenAI**: not emitted yet; the attributes are still "Development".
- **SLSA / AI authorship**: ADR 0015.

## What to take away

Choose Choregos when the question is *"what did the agent do, under which contract, who
signed off, what did it cost, and can I prove it?"* — and expect to bring, or wait for, a
runtime for density and suspend, a detector for injection, and breadth for tools.
