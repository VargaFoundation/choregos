# 0016 — An `agent-sandbox` executor: warm pools, suspend across a human gate, snapshots

- **Status**: proposed, 2026-09-25 — not implemented; revisits ADR 0013
- **Concerns**: the `Executor` protocol, the chart, ADR 0013 (density)

## Context

ADR 0013 laid a seam: an executor **announces** `queue`, `suspend`, `resume`, `snapshot`,
and a conformance test states the fact that no executor could snapshot. It also refused a
pool of warm runners for a precise reason: a pod that chains two runs holds, at some
instant, more than one run's credentials — and the boundary "one pod, one run, one token"
is one Kubernetes holds, not our code.

`kubernetes-sigs/agent-sandbox` (v1beta1, 2026) provides exactly the missing pieces:

- a `Sandbox` resource with **suspend/resume** semantics: the pod is deleted, the volume and
  the network identity are kept, the pod comes back with the same state;
- a `SandboxWarmPool` that pre-creates sandboxes from a template, and a `SandboxClaim` that
  hands one out — from a **clean template image**, never from a previous run;
- `RuntimeClass` (gVisor, Kata) as a first-class field of the template.

What it does not provide: a stage, a gate, a ledger, a ticket. It is a runtime. That is the
posture of `docs/positioning.md`: Choregos is the delivery layer, and this is the first
runtime it sits on that can do what Jobs cannot.

## Decision

A fifth executor, `agent_sandbox`, that announces the four capabilities **and implements
them**:

| Capability | Implementation |
| :-- | :-- |
| `queue` | a `SandboxClaim` waits when the pool is empty; no pod, no image pull — the same admission discipline as ADR 0013's suspended Jobs |
| `suspend` | when a stage ends on `needs_human` with `on_question`, the sandbox is **suspended**, not destroyed: the workspace, the agent's own state and the ACP session survive the wait |
| `resume` | the human decision resumes the sandbox; the agent continues with its context instead of replaying the stage from scratch (today: a full replay, and its cost, ADR 0013 Decision 3) |
| `snapshot` | the warm pool hands out sandboxes from the template snapshot; a run never inherits another run's filesystem |

### What keeps ADR 0013's security reasoning intact

The run token is still mounted **per sandbox**, by reference, and the sandbox is destroyed
at the end of the run. A warm sandbox is one that has **never run anything**: it holds no
credential until a run is assigned, and it receives exactly one run's token. The boundary
does not move from Kubernetes to our code; it is the sandbox, and it is still held by the
operator's CRD, not by us. The warm-runner pool ADR 0013 refused was a pool of *used*
runners; this is a pool of *unused* ones.

### Conditions, honestly

- The operator's CRDs are **cluster-scoped**. A tenant of a shared platform (AppProject with
  `clusterResourceWhitelist: []`) cannot install them — the same objection that ruled out
  AX's control plane. `agent_sandbox` is therefore an executor a platform team enables, not
  a default. `tekton` and `k8s_job` stay.
- `v1beta1`: the API may still change. The executor pins the API version and the
  conformance suite runs against a real cluster (`tests/cluster`) before any capability is
  announced — an announced, absent capability is worse than an absent one (ADR 0013).
- Suspend across a human gate means a sandbox that **waits**, with its volume, for hours or
  days. The policy bounds it: `sandbox.suspend_max_hours`; beyond it, the sandbox is destroyed
  and the stage replays as today.

## Consequences

- The `Executor` protocol gains `suspend(ref)` and `resume(ref)`; the interpreter calls them
  around `await_human` when the executor announces the capability, and does nothing otherwise.
- ADR 0013's fact-stating test ("no executor can snapshot") turns red the day this ships:
  that is its purpose, and this record is what it points to.
- The chart gains `runner.executor: agent_sandbox`, `runner.warmPool.size`, and a
  `RuntimeClass` value; Kyverno's non-root rule applies to the sandbox template.
- The bench cannot prove this: kind has no agent-sandbox operator. A cluster test does.

## Alternatives discarded

- **Implementing suspend on Jobs ourselves** (checkpoint the container with CRIU): fragile,
  privileged, and exactly the code we should not own.
- **AX as the runtime**: unsupported by its own vendor, cluster-scoped, and its primitives are
  a subset of agent-sandbox's (ADR 0013).
- **Doing nothing**: a human gate costs a full stage replay, and the density ceiling stays
  where the image pull puts it.
