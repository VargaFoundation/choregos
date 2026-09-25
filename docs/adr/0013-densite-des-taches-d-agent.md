# 0013 — Agent task density, and what we take from AX

- **Status**: accepted, 2026-09-24
- **Concerns**: the execution of agent stages (`Executor`), the chart, operations

## Context

Google published [AX](https://github.com/google/ax) (Apache 2.0), a declarative
orchestrator of agent tasks: sandbox, workspace, network gateway, model, suspend/resume, on
a Kubernetes control plane. It announces "billions of agent workloads in a cluster", on top
of [Agent Substrate](https://cloud.google.com/blog/products/containers-kubernetes/bringing-you-agent-sandbox-on-gke-and-agent-substrate)
and gVisor.

The question is not "should we adopt it" but **what it does that we do not**. And a real
problem hid behind it: Choregos launches **one Job per agent stage**, with no cap at all.
Ten tickets starting together make ten pods pulling the same image at once, on a cluster
that has other things to do.

## What AX does, and where we stand

| AX capability | Here |
|---|---|
| Sandbox per task, CPU/memory limits | `Executor` × 4 (Tekton, Kubernetes Job, ACA, Docker), gVisor by `RuntimeClass` |
| Allowlist network gateway | NetworkPolicies and CiliumNetworkPolicy, **egress verified on a cluster** (7 tests) |
| Model and credentials | Model profiles, a virtual key **per run** with a hard cap, cost in the ledger |
| Workspace (git + MCP pre-wired) | `StageInput`: repository, branch, MCP servers, playbook — per run |
| Phases and conditions | Run states, events, resumable SSE |
| **Cap and queue** | **Nothing. That is the gap, and it is fixed here.** |
| **Suspend/resume of an agent IN FLIGHT** | Nothing, and it is not within our reach (see below) |
| `ax ssh` into the sandbox | Deliberately refused (see below) |

## Decision 1 — The queue is made by Kubernetes itself

`runner.maxActive` bounds the number of **simultaneous** runs per namespace. Beyond it, the
Job is created with `spec.suspend: true`: it has no pod, pulls no image, takes no room in
the quota. Admission happens along status polls — the orchestrator already queries every
run, so every loop turn is an opportunity to admit the next — and **in arrival order**,
without which the last ticket filed would jump the queue every time.

This is not a scheduler, and the cap is **soft**: two runs can admit themselves in the same
window and overshoot by one. That is the price of adding no component, and it is nothing
next to the problem avoided. `0` keeps the previous behaviour: a deployment that asks for
nothing does not change regime overnight.

The one-node bench moves to `maxActive: 2`, because that is exactly where the spike hurts.

## Decision 2 — No pool of warm runners, and the reason is security

Maximum density would come from a pool of warm runner pods chaining stages: no more pod
creation, no more image pull, warm caches. We **refuse**, and it has to be said why, because
the temptation will come back.

Today a run's token is a Secret mounted **by reference** in that run's pod, and it dies
with it. A pod that chains two runs necessarily holds, at some instant, what it takes to
obtain the second's credentials — hence a right wider than a single run. We would replace a
boundary Kubernetes holds (one pod, one run, one token) with a boundary our code would claim
to hold. The isolation boundary stays the **run**, not the project.

What would make the pool acceptable: a sandbox able to restart from a clean snapshot between
two runs. That is precisely what Agent Substrate brings — and the real reason to look at AX
later, not its primitives.

## Decision 3 — What we do not take, and why

- **Agent Substrate as a dependency**: Google itself marks it "not an officially supported
  Google product". AX also announces major breaks before stabilising. We would trade proven
  code for an unsupported dependency.
- **AX's control plane** (`ax-system`, cluster-scoped resources): a tenant of our target
  platform is not allowed to create any — the AppProject forbids it. AX would be unusable
  where we deploy.
- **`ax ssh` into the sandbox**: our tenant Role deliberately excludes `pods/exec`. A human
  entering an agent's pod sees its secrets and can act in its name, outside any trace. The
  run's journal, transcript and evidence are the answer — and they are archived, which an
  interactive session is not.
- **Suspend/resume of a stage IN FLIGHT**: Kubernetes cannot suspend a started Job without
  destroying its pod. Doing it properly requires a sandbox snapshot. Honestly open: today a
  node lost mid-stage makes us **replay the whole stage**, and pay for it again.

## Decision 4 — The seam is laid now, not the day we need it

An executor **announces what it can do** (`capabilities`), from a closed vocabulary:
`queue`, `suspend`, `resume`, `snapshot`. Today a single executor fills a single capability
— `k8s_job` can queue. That is precisely why it must be laid now: the day a snapshotting
sandbox arrives, the orchestrator will **ask** what the runtime can do instead of assuming
it, and nothing else will move.

A conformance suite refuses an executor announcing a capability it does not implement: an
announced, absent capability is worse than an absent one, because the caller relies on it.
It also carries a test that states a **fact** rather than a rule — "no executor can take a
snapshot yet". The day it fails is the signal to re-read this ADR: the warm-runner pool
becomes defensible again, and losing a node stops replaying everything.

## Consequences

- `runner.maxActive` in the chart, `CHOREGOS_RUNNER_MAX_ACTIVE` for the `k8s_job` executor.
- A waiting run says so: `waiting for a slot (cap N per namespace)`.
- Three tests without a cluster: the cap holds, the queue advances in order, and without a
  cap nothing changes.
- A defect found on the way: our Kubernetes client overwrote the caller's headers, which
  made every `PATCH` impossible — it has to announce its
  `Content-Type: application/merge-patch+json`.
