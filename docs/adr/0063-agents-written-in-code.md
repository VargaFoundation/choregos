# 0063 — Agents written in code

- **Status**: accepted, 2026-10-15
- **Concerns**: the stage input (`stage-input.schema.json`, `AgentRef.image`), the agent registry
  (`AgentSpec.runtime`, `GET /platform/agent-images`), the orchestrator's stage preparation, the
  runner (`code` backend), the chart; builds on [ADR 0002](0002-acp-as-the-agent-contract.md) (ACP is the
  seam), [ADR 0033](0033-agent-registry-and-skill-library.md) (what an agent version is) and
  [ADR 0056](0056-a-deterministic-step.md) (operator-named images)

## Context

Every agent the platform ran was a coding agent the runner ships (Claude Code, Codex, OpenCode…),
steered by instructions. Some agents are better written as code: a fixed pipeline with one or two
model calls, a LangGraph graph, a program that already exists in Python. Teams asked to bring those
under the platform **with** its guarantees — permissions, budget, journal, limits, conformance — and
not around them.

Three shapes were compared:

| | A. an ACP agent in an operator-named image | B. a job-like contract (read the stage input, write `result.json`) | C. a git reference installed at run start |
| :-- | :-- | :-- | :-- |
| Governance kept | all: permission requests through the runner's guard, the turn guard, `max_turns`, the tool-call journal, cancellation, conformance | loses permission gating, turn accounting, the live journal | as A or B |
| Platform change | small: one backend, image resolution | a second agent loop in the runner | package installation at run time |
| Supply chain | image pinned by digest, chosen by the operator | same | mutable references, transitive dependencies, egress to a package index |

## Decision

1. **A code agent is an ACP agent** (option A). The runner starts it as a subprocess and speaks ACP on
   stdio, exactly as for any backend; what the platform guarantees for a coding agent applies
   unchanged. B is what the `job` actor already is, for code that needs no model. C is refused for
   its supply chain.
2. **`AgentSpec.runtime`** is a discriminated union on `kind`. Absent, it means `acp`: today's
   meaning, the coding agent named by `backend`. `{kind: code, image, command, api_format}` is a code
   agent: `image` is a **name** of the operator's `CHOREGOS_AGENT_IMAGES` map, never a reference;
   `command` is the argv started in that image; `api_format` (`openai` by default, or `anthropic`)
   says which model API the code calls through the gateway. Further runtimes (remote agents) add a
   `kind` to the same union. A `code` runtime together with `backend` is refused (422): two answers
   to one question.
3. **The image is the operator's.** `CHOREGOS_AGENT_IMAGES` is a `name -> reference` map, like
   `CHOREGOS_JOB_IMAGES`. The API refuses, at publication, a version naming an unknown image (the
   known names are in the error); the orchestrator resolves the name at stage start and fails the
   step **without a retry** on an unknown one. `CHOREGOS_REQUIRE_IMAGE_DIGEST` (true in the chart)
   makes a reference without `@sha256:` stop the API and the workers at start-up, for both maps.
   `GET /platform/agent-images` lists names and a `digest_pinned` flag, never the references.
4. **The stage input carries the resolved reference** in `AgentRef.image`; the executor starts the pod
   in `job.image or agent.image or runner_image`. The orchestrator fixes `agent.backend = "code"` and
   `agent.launch.command` from the runtime; an actor that names another backend for a code agent is
   refused without a retry (`AgentIndisponible`). The `code` backend is a **runtime** backend: it is
   not in the rotation of `profile:by_size` or cross-backend review, and not in the conformance list
   of shipped backends.
5. **The `code` backend** launches the version's argv, passes the model only through the environment
   (`OPENAI_*` or `ANTHROPIC_*` from the run's gateway key; none when the run has no key, so the pod's
   own credentials are never overwritten) and `CHOREGOS_MAX_TURNS`. The code gets no secret that any
   other agent would not.
6. **The same sandbox, network policy, run token and run key** apply: the code runs in the pod like
   any agent. Images are built **from** the runner image; the runner is the entrypoint.

## Consequences

- A contract change: `AgentRef.image`, `AgentSpec.runtime`, `listAgentImages`. Old stage inputs and
  old versions are valid as they are; a version without a runtime keeps its digest.
- The `agent_sandbox` executor claims from one warm pool, built from the runner image: it refuses a
  run whose image differs (PR 17a), so a code agent needs `k8s_job` (or another executor with a pod
  per run). The refusal is explicit, never a silent substitution.
- Cosign verification of agent images is not built; the digest pin and the operator's list are the
  control for now.
- `choregos-agent-sdk` (`packages/agent-sdk`, Apache-2.0, built on the official ACP SDK
  `agent-client-protocol`) hides ACP from Python authors; see [code-agents](../code-agents.md). Its
  obligations: every tool call and write goes through `session/request_permission` before it runs,
  model and tool calls count as turns and stop at `max_turns`, the result is validated against
  `StageResult` before it is written, standard output stays the protocol's. It is a convention, not
  a wall: code in the pod is bounded by the sandbox.
- Code agents pass the seven conformance checks (`tests/conformance/backends/test_code_agents.py`;
  the plain example in CI, the LangGraph example by a manual workflow, an image by
  `make conformance-agent`). A code agent is not scripted, so the checks observe a deliverable the SDK
  writes through the guard rather than the shipped backends' `hello.py`.
- Publishing `choregos-agent-sdk` and `choregos-contracts` to PyPI is a release gesture (trusted
  publishing, `release.yml`, off until `PUBLISH_TO_PYPI` is set); resume of a code agent
  (`session/load`) is not supported in v1.

## Amendment (2026-10-16): no Choregos framework

Asked whether a custom agent framework is warranted against the market's (LangGraph, OpenAI Agents
SDK, Google ADK, Pydantic AI, CrewAI, Microsoft Agent Framework, smolagents, Strands, Mastra), the
answer is **no**, and `choregos-agent-sdk` stays what it is:

1. **A run-contract adapter, not a framework.** Its scope is the four things a step needs: ACP to
   the runner, MCP tools through the runner's permission path, the gateway as the model with the
   run's key, `result.json`. No control-flow model, prompt templates, memory or multi-agent
   abstractions. Every framework needs only a model endpoint and an MCP endpoint, so every framework
   runs inside it. Durable state, retries and human gates are the workflow's (Temporal). The public
   surface is frozen by `packages/agent-sdk/tests/test_public_surface.py`; widening it needs an
   amendment of this ADR.
2. **Two gaps closed.** A framework's own model client escaped turn accounting, and its native MCP
   client escaped the permission path. `ctx.llm.http_client()` / `openai_client()` count every
   model request as a turn (refused before it leaves at `max_turns`, journaled, streaming intact);
   `integrations.{langchain,openai_agents,pydantic_ai}` build a framework's tools from the run's
   tools so each call goes through the guard. Frameworks are never dependencies of the SDK. The
   conformance check `max_turns` now compares the gateway's request count with the limit, so a
   client the SDK does not count fails it. This is cooperation, not a boundary (the agent runs as
   the runner's uid): loopback proxies the runner governs (a model proxy, an MCP proxy) would make
   native clients governed without bridges, and are not built.
3. **The protocols, named.** ACP is the Agent Client Protocol (Zed), the agent seam of ADR 0002;
   it is not IBM's Agent Communication Protocol, which merged into A2A (Linux Foundation) in 2025.
   A2A is the remote-agent path, not the in-pod one. `agent-client-protocol` 0.12.1 is pinned; the
   1.0 release candidate is followed, the pin moves when 1.0 is final and 14 days old.
4. **Where it lives.** In this monorepo (`packages/agent-sdk`), in lockstep with
   `choregos-contracts`, the runner image and the conformance suite; no separate repository. It is
   published to PyPI from this repository's GitHub Actions (`release.yml`, trusted publishing),
   because PyPI trusted publishing accepts GitHub Actions and not Tekton, the CE's contributors are
   not on the Diametral platform, and its images are already signed keylessly with GitHub OIDC.

