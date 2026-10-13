# 0053 — The `agent_sandbox` executor as built: a pushed assignment, a queue we hold, a clean sandbox

- **Status**: accepted, 2026-10-12 — amends [ADR 0016](0016-executeur-agent-sandbox.md); stream S35
- **Concerns**: `packages/adapters/.../executor/agent_sandbox.py`, `choregos-runner await-assignment`,
  the chart (`runner.executor: agent_sandbox`), the conformance suite

## Context

ADR 0016 decided an executor on `kubernetes-sigs/agent-sandbox` and described it in terms of the
operator's primitives. Building it on the pinned release (v1.0.6, API `v1beta1`) showed three
places where those primitives do not do what 0016 assumed. This record says what was built
instead, so that the next reader does not take 0016's table for the implementation.

## Decision 1 — The run token is pushed to the claimed pod, not mounted

0016 kept "the run token mounted per sandbox, by reference". A warm pod cannot mount it: its
spec is made before any run exists, a pod's volumes cannot change afterwards, and the only
per-claim injection the operator offers (`SandboxClaim.spec.env`) forces a cold start and writes
the value, in clear, into the claim's spec.

So the token is **pushed**:

- the run's token lives in a Secret named after the run, owned by its SandboxClaim — the
  reference, as with `k8s_job`; no runner pod can read it (no service account token, no RBAC);
- the warm pod runs `choregos-runner await-assignment`, listening on one port;
- once the controller has bound a sandbox to the run's claim and the sandbox is Ready, the
  executor reads the Secret and POSTs `{run_id, api_url, run_token, env}` to **that** pod's IP;
- the runner accepts one run for the life of the process: the same run again is 200, another
  run 409 — and a 409 fails the run instead of sharing the sandbox.

What holds: a warm sandbox holds no credential; exactly one pod receives a given token, after it
was chosen for that run. What it costs: the token crosses the pod network in HTTP inside the
cluster (encrypted only if the CNI encrypts), and a pod of the same namespace that pushes first
can occupy a warm sandbox — with its own token, gaining nothing, and the run then fails visibly.
Deployment-wide secrets a `k8s_job` pod mounts with `envFrom` are pushed with the assignment for
the same reason.

## Decision 2 — The queue is the executor's, not a waiting claim

0016 said "a SandboxClaim waits when the pool is empty". In v1.0.6 it does not: an empty pool
makes the controller **cold-start** a sandbox from the template — a pod, an image pull. A run
beyond `runner.maxActive` therefore waits as its Secret alone (`choregos/queued=true`), with no
claim, and is admitted in arrival order along status polls: ADR 0013's discipline, soft cap
included. Reading that order lists the namespace's Secrets by label, so the executor's Role
gains `list` on `secrets` — on top of the `get` it already has on all of them.

## Decision 3 — `snapshot` means a clean sandbox, and needs no method

ADR 0013 defined `snapshot` as "freeze the sandbox's state and restore it identically", and the
conformance suite required a `snapshot()` method. 0016 uses the word for what makes a warm pool
acceptable: every run starts from the template, never from a previous run's filesystem. That is
what is built — a sandbox is claimed once and deleted with its claim — and it is a property of
`start`, like `queue`, not a call the orchestrator makes. The vocabulary's definition and the
conformance rule follow 0016; freezing a running sandbox is not offered by the operator and is
not claimed.

## Conditions

- The template and the pool live in the release namespace; a per-project `namespacePattern` is
  refused at render time until the provisioning writes them per project.
- The template's RuntimeClass is the pool's: a run whose policy asks for another one is refused,
  never run without it.
- Everything a cluster must prove is in `tests/cluster/test_agent_sandbox.py`, which is skipped
  where the operator is absent and says so.
