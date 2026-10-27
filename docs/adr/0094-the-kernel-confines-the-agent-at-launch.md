# 0094 — The kernel confines the agent's process at launch, inside the warm pod

- **Status**: accepted, 2026-10-27 — implemented by S44-01
- **Concerns**: `choregos_runner.confine`, the agent launch in `choregos_runner.runner`
  (`Runner._confine`), the runner setting `CHOREGOS_RUNNER_CONFINEMENT`, the exit code
  `Exit.CONFINEMENT_UNAVAILABLE` (90); builds on [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md)
  (network per run), [ADR 0072](0072-agent-sandbox-is-the-runtime-of-a-real-deployment.md) (the warm
  pool) and [ADR 0084](0084-guardrails-outside-the-agent.md) (guards outside the agent)

## Context

ADR 0070 rates the runner's network guardrail "advisory": it judges what the agent *asks* (an ACP
permission request), and a shell command never asks. `curl --noproxy '*' https://…` from the
agent's Bash reaches any host the NetworkPolicy lets through, and on a tenant whose infrastructure
owns that policy, eight hosts are open directly. The agent also runs as the runner's uid in the
same container, and `/app` (the runner's code) and `/etc/choregos` are owned by that uid: the agent
can rewrite the code that judges it.

A per-run NetworkPolicy was rejected by ADR 0070 because warm sandboxes exist before their run. On
2026-10-26 NVIDIA OpenShell was reviewed as a runtime for this; it closes the same gaps (Landlock,
seccomp user-notification on `connect`, a supervisor) but its enforcement only starts with its own
gateway, which creates its own pods, two per sandbox, and so cannot use our warm pool. Its binaries
alone enforce nothing (the sandbox waits for gateway-signed bootstrap material), and it does not run
under gVisor. The owner kept the warm pool.

## Decision

1. **The runner wraps the agent's command.** Between the assignment and the agent, the runner launches
   `python -m choregos_runner.confine --policy <json> -- <agent command>`, which restricts itself
   with Landlock, then `exec`s the agent. The domain is inherited and can never be left: per ADR 0084
   the guard is the kernel's, not the agent's. The warm pool, the sealed assignment and the runner
   are untouched; nothing is added to the pod.
2. **Writes** are allowed only beneath the workspace, the temporary directory (the agent's `$HOME`
   lives there) and `/dev`. The runner's code, its configuration and every other path are read-only
   to the agent.
3. **TCP connections** (Landlock ABI 4) are allowed only to the egress proxy's port and to the ports
   of the platform endpoints the run reaches directly — the API, the tool servers, the model gateway
   and the MCP servers of the run, kept only when `NO_PROXY` routes them around the proxy or they
   are on loopback. With no egress proxy configured, connections are not filtered: closing ports
   would only break a run whose traffic goes out directly.
4. **Landlock filters ports, not hosts.** Which host a port reaches remains the NetworkPolicy's job,
   and which public host is allowed remains the proxy's (ADR 0070). UDP, hence DNS, is not filtered.
5. **A Landlock domain cannot `ptrace` a process outside it**, so the agent cannot read the runner's
   memory or `/proc/<runner>/environ`.
6. **Three modes, set per deployment** by `CHOREGOS_RUNNER_CONFINEMENT` (in `runner.env` of the chart):
   `off`; `best_effort` (the default) applies what the kernel supports and launches; `enforce` refuses
   to start the agent when the kernel cannot honour the policy, and the run ends with exit code 90.
   What applied is journaled before the agent starts: `agent.confinement` with the mode, the Landlock
   ABI, `files`, `network`, the write roots, the open ports and what was missing.
7. **No seccomp filter of our own.** `RuntimeDefault` plus `drop: ALL` already refuse raw and packet
   sockets, user namespaces and unprivileged `bpf`; `ptrace` of the runner is covered by Landlock (§5).

## Consequences

- On a kernel of 6.7 or later (Landlock ABI 4), `curl --noproxy` to a public host on 443 is refused by
  the kernel, whatever the agent does, on every executor that launches a local agent (agent_sandbox,
  k8s_job, Tekton, local Docker).
- Under gVisor the Landlock syscalls do not exist: `best_effort` launches unconfined and journals
  `missing: ["landlock"]`; a project that needs both sets gVisor, not `enforce`. Older kernels (5.13 to
  6.6) get the write rule without the port rule.
- A tool that writes outside the workspace and `$TMPDIR` (a global cache under `/var`, a path in
  `/app`) fails with `EACCES`. That is the point for `/app`; for the rest, the tool must use `$HOME`.
- Hosted agents (ADR 0080) run in a sidecar container, not as the runner's child: they are not
  wrapped, and keep their loopback proxies.
- Not proved on the production nodes: their kernel version was not read when this was decided.

## Alternatives rejected

- **OpenShell as the runtime** — see Context: its own pods, no warm pool, no gVisor, a gateway to run.
- **OpenShell's supervisor alone** (`--role=network-proxy`) — an L7 proxy that only proxy-aware
  clients use; it does not close the `curl --noproxy` path, which is the gap.
- **A per-run NetworkPolicy** — rejected by ADR 0070 for the warm pool.
