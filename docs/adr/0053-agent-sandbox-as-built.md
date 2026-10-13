# 0053 — The `agent_sandbox` executor as built: a sealed assignment, a queue we hold, a clean sandbox

- **Status**: accepted, 2026-10-12 — amends [ADR 0016](0016-executeur-agent-sandbox.md); stream S35
- **Concerns**: `packages/adapters/.../executor/agent_sandbox.py`, `choregos-runner await-assignment`,
  the chart (`runner.executor: agent_sandbox`), the conformance suite

## Context

ADR 0016 decided an executor on `kubernetes-sigs/agent-sandbox` and described it in terms of the
operator's primitives. Building it on the pinned release (v1.0.6, API `v1beta1`) showed three
places where those primitives do not do what 0016 assumed. This record says what was built
instead, so that the next reader does not take 0016's table for the implementation.

## Decision 1 — The run token is pushed, sealed to the claimed pod, not mounted

0016 kept "the run token mounted per sandbox, by reference". A warm pod cannot mount it: its
spec is made before any run exists, a pod's volumes cannot change afterwards, and the only
per-claim injection the operator offers (`SandboxClaim.spec.env`) forces a cold start and writes
the value, in clear, into the claim's spec.

So the token is **pushed, sealed to the one pod that will run it**:

- the run's token lives in a Secret named after the run, owned by its SandboxClaim — the
  reference, as with `k8s_job`; no runner pod can read it (no service account token, no RBAC);
- the warm pod runs `choregos-runner await-assignment`: at boot it makes an X25519 key pair in
  memory and prints the public key on one line of its stdout, then listens on one port;
- once the controller has bound a sandbox to the run's claim and the sandbox is Ready, the
  executor reads that line **through the API server** (`pods/log`, already in its Role). The API
  server reaches the kubelet over authenticated TLS: nobody on the pod network can substitute
  their own key. A pod that printed no key gets nothing;
- the executor seals `{api_url, run_token, env}` to that key — an ephemeral X25519 exchange,
  HKDF-SHA256, ChaCha20-Poly1305 with the run id as associated data (`choregos_adapters.sealing`,
  on `cryptography`, already a dependency) — and POSTs `{run_id, sealed}` to **that** pod's IP;
- the runner opens it in memory and drops its private key: it can open one assignment, ever.
  What does not open with its key for that run id is refused; the same run again is 200,
  another run 409 — and a 409 fails the run instead of sharing the sandbox.

What holds: a warm sandbox holds no credential; exactly one pod can read a given token, after it
was chosen for that run; the pod network only ever carries the run id and ciphertext — the
deployment-wide secrets a `k8s_job` pod mounts with `envFrom`, pushed with the assignment, are
sealed with it.

Who may push at all: the chart renders, with the template, the NetworkPolicy
`choregos-runner-assignment`. It selects the sandbox pods (`choregos/sandbox: "true"`) and admits
to the assignment port only the orchestrator's `executor` workers of the release namespace. The
sandbox pods deliberately do not carry `app.kubernetes.io/part-of: choregos`, which
`choregos-allow-ingress` opens to the whole namespace: NetworkPolicies add up, and that one would
undo this one (`tests/charts/test_agent_sandbox.py` checks no other policy selects them). So a
pod of the namespace can no longer push first and occupy a warm sandbox with a token of its own.

What remains: the policy holds only where the CNI enforces NetworkPolicies (an unenforcing CNI
accepts it and lets everything through — the cluster test `test_agent_sandbox_executor.py`
checks the push from another pod is blocked); a pod that impersonates the orchestrator's labels
needs the right to create pods with them in the release namespace, i.e. to be the platform. The
public key is in the pod's log, which is public by nature. No CiliumNetworkPolicy variant: the
repository ships none for runners, and Cilium enforces this standard policy.

### Alternatives discarded for the push

- **mTLS between executor and runner, or a SPIFFE identity** ([ADR 0021](0021-spiffe-derriere-le-jeton-de-run.md)):
  0021 is *proposed*, not implemented; it needs SPIRE, cluster-scoped, on top of the operator.
  A per-pod key read through the API server gives the property needed here — only that pod
  reads the token — with nothing to install.
- **Relying on the CNI's encryption** (WireGuard, IPsec): a property of one cluster's
  configuration, invisible to the platform and absent from many; the token would be in clear
  wherever it is off.
- **The token in clear over HTTP inside the cluster**: the first version of this record. Anyone
  able to read the pod network read the token.

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
