# 0146 — The runner and the agent do not share a container

- **Status**: proposed, 2026-11-11 — design study S65-04; nothing is implemented. Would amend
  [ADR 0094](0094-the-kernel-confines-the-agent-at-launch.md) (what the confinement is still for),
  [ADR 0115](0115-the-agent-never-holds-the-run-token.md) (closes its first consequence) and
  [ADR 0002](0002-acp-as-the-agent-contract.md) ("in-pod stdio"); extends
  [ADR 0080](0080-hosted-prompt-and-voice-agents.md) §4-5 to the steps an ACP agent carries
- **Concerns**: `choregos_runner.acp.client.AcpClient` (how the agent is started),
  `choregos_runner.loopback` (model, MCP, CONNECT), `choregos_runner.workspace` (the runner's git),
  `choregos_runner.dod` (where the checks run), `choregos_runner.confine`, the executors (`k8s_job`,
  `agent_sandbox`, `local_docker`, `tekton`, `aca`) and their `capabilities`, the chart's
  SandboxTemplate; stories in
  [PLAN-2026-11-11-runner-agent-separation.md](../plan/PLAN-2026-11-11-runner-agent-separation.md)

## Context

Paths below without a directory are under `packages/runner/src/choregos_runner/`; the executors
(`k8s_job.py`, `agent_sandbox.py`, `local_docker.py`, `tekton.py`, `aca.py`) are under
`packages/adapters/src/choregos_adapters/executor/`, and `sandbox.yaml` is the orchestrator chart's
template. Every line was read at `9f646fda4`.

A step an ACP agent carries runs two things in one container, under one user id (1000:
`k8s_job.py:269-274`, `sandbox.yaml:69-73`, `docker/runner.Dockerfile:267`):

- the **runner**, which is trusted. It holds the run token, the gateway key, the remote token and the
  git token; it answers the agent's permission requests (`guardrails.py`), counts its turns, runs the
  project's checks, measures the diff, reverts what is out of scope, commits, pushes and posts the
  result;
- the **agent**, which is not: a coding-agent CLI driven by a model, started by the runner as its
  child, with pipes on its standard input and output (`acp/client.py:156-164`).

Three mechanisms keep the second away from the first's credentials. Each was added after the sharing
showed, and each leaves something:

| Mechanism | Where | What it leaves |
| :-- | :-- | :-- |
| The agent's environment starts without the `CHOREGOS_RUN_*` variables (ADR 0115, S57-02) | `config.py:26-43` | Under `k8s_job`, ACA, local Docker and Tekton the runner's own environment still holds them (`k8s_job.py:284`, `aca.py:185`, `local_docker.py:80`), and `/proc/<runner>/environ` gives an environment to any process of the same user. Probed for this study (Linux 6.18, `kernel.yama.ptrace_scope` 1): a child started with a stripped environment reads the variable in its parent's `/proc/<pid>/environ` |
| Landlock around the agent's command (ADR 0094) | `confine.py`, applied at `runner.py:739` | It does not exist under gVisor, which is where a deployment that asks for gVisor runs every agent: `docs/plan/STATUS.md`, S44-01b, records that the child then does all five things probed, reading the runner's environment among them. And it wraps the agent's command only |
| The runner's git ignores the repository's hooks, drivers and helpers (#1345) | `workspace.py:72-79`, `:256-267` | A list, kept by hand, of the ways a repository's configuration makes git run a program — in a repository the agent writes, for a push that carries the git token on its command line (`workspace.py:485-502`). git's manual says running git in a `.git` directory of untrusted origin is not safe (git(1), "SECURITY") |

Two more facts are in the code:

- **The project's checks are not confined.** `dod.run_checks` starts the project's test, lint and
  typecheck commands with `run_command` (`dod.py:145`, `workspace.py:189`): a shell of the runner's
  container, not wrapped by `confine`. They run the code the agent has just written, as the runner's
  user, on every kernel. `Runner._hosted_checks` (`runner.py:1034-1059`) says so in its own words and,
  for a hosted agent that wrote the tree, skips them.
- **The agent holds credentials by design**: the gateway key in its environment (`runner.py:367`,
  `backends/base.py:131`, `:144`), the egress token in its proxy variables (ADR 0115, first
  consequence), and the headers of the run's MCP servers in `session/new` (`backends/base.py:91`).

`SECURITY.md` calls the runner's guardrails "a net, not a wall", and ADR 0084 §1 puts every guard "in
the runner process the agent cannot rewrite". The second sentence is true only where Landlock is: `/app`,
the runner's code, belongs to the agent's user (`docker/runner.Dockerfile:242`).

Hosted agents (ADR 0080 §4-5) are already built the other way round: the agent's container runs beside
the runner's (a native sidecar, `k8s_job.py:355-400`; a second container in the runner's network
namespace, `local_docker.py:125-159`), it is given no secret, and it reaches the model, the tools and the
network through three listeners the runner serves on loopback (`loopback/model.py`, `mcp.py`,
`connect.py`; ports 4001-4003 in `choregos_core/hosted.py:22-25`). The question of S65-04 is whether a
step an ACP agent carries can take that shape, and what each guarantee then becomes.

## Decision

### The shape

1. **Two containers in the run's pod: the runner's holds every credential and takes every decision,
   the agent's holds none and takes none.** The boundary is the container's — its own mount and PID
   namespaces — and no longer the user id's or Landlock's. From the agent's container there is no
   `/proc/<runner>`, no `/app` of the runner, no file of the runner's that a volume does not share
   on purpose (§4); no executor asks for a shared process namespace. The two still share the pod's
   network and IPC namespaces: a container gives nothing against what the agent connects to (§11).
2. **The agent's container starts a launcher, never an agent.** Its entry point is a new command of
   the runner package (`choregos-runner launcher`; standard library only, as `confine.py` is). It
   listens on one loopback port and starts what the runner asks for: the harness, and later the
   project's checks (§6). For each process the runner sends a command, an environment and a working
   directory; the launcher carries its standard input, output and error, and its exit code, over that
   connection. Before any request the launcher holds nothing and has started nothing, which is what a
   warm sandbox needs.
3. **ACP still travels on the harness's own stdio.** The harness is the launcher's child and reads and
   writes its pipes as today, so no harness has to speak anything new: the commands of
   `backends/claude_code.py:34` and `backends/others.py:25`, `:59`, `:90`, `:163`, `:248` are started
   unchanged. `AcpClient` reads a stream instead of a pipe; its protocol code does not move. ADR 0002's
   "in-pod stdio" becomes: stdio at the harness, a loopback stream between the two containers.
   The launcher listens and the runner connects, over loopback TCP, because that is how the runner
   already reaches a hosted container (`hosted.py:53`, `wait_ready`), on Kubernetes, on Docker and
   under gVisor. Nothing authenticates the launcher: whatever answers on that port is in the agent's
   container, which is untrusted whoever speaks. The runner opens no listener for the launcher.
4. **Two volumes are shared, and they are listed.** The working tree (`/workspace`), which both
   read and write; and an exchange directory for the tool client the runner writes and the scripts
   the agent writes (ADR 0122; `tool_scripts.py:145`, `:150`, today under the shared `$TMPDIR`).
   Private to the runner, out of the agent's reach: its git directory (§5), the run's transcript and
   the saved session id (today in the tree: `runner.py:270`, `:1390`), the result it posts. Private
   to the agent: its home (`workspace.py:615-622`). Where a sandbox suspends, all of them are
   sub-paths of the run's one volume claim, each mounted only where it belongs.

### Credentials

5. **The runner runs git, in a repository that is not on the shared volume.** Its git directory lives
   on its private volume and names the shared tree as its work tree (`--git-dir`, `--work-tree`); the
   fetch, the diff, the scope check's `checkout`, the commit and the push with the token all read that
   repository and no other. The tree keeps a `.git` of its own, prepared by the runner before the agent
   starts, without a credential, for the agent's `git log` and `git diff`; the runner never reads it
   again. Probed for this study (git 2.53.0): with the git directory outside the tree, a `core.fsmonitor`
   program, a `filter` driver and four hooks planted in the tree's own `.git` ran zero times across
   `status`, `add -A` and `commit`, where the same `status` run in the tree ran one; the tree's `.git`
   was not committed. The hardening of #1345 stays, as the second lock.
6. **The runner executes nothing the agent can write, and follows no link there.** The project's
   checks and its `dod.facts` commands are started by the launcher, in the agent's container, at the
   runner's request: the command comes from the project's configuration, the exit code and the output
   come back on the connection. Every path the runner opens in a shared volume — the agent's
   `result.json`, its deliverables, the files the ACP file methods read and write
   (`acp/client.py:463-534`) — is opened the way `hosted_workspace.py` opens the copy it brings back:
   from its parent's descriptor, with `O_NOFOLLOW` (`hosted_workspace.py:45`, `:185-240`). ADR 0080 §7
   gave the reason: a symlink the agent plants is followed by the runner's own commands.
7. **The agent reaches the platform through the runner's three listeners (ADR 0080 §5) and holds no
   key.**
   - *Model (4001).* The harness gets the loopback address and the constant key
     (`choregos_core/hosted.py:27`); `ModelProxy` forwards with the run's key and forces the run's
     model (`loopback/model.py:94`, `:121`). It must stop counting each model request as a turn for
     an ACP step (`model.py:88`): a coding CLI sends a model request for every tool call and side
     requests of its own (the aliases pinned in `backends/claude_code.py:96-105` are theirs), and
     `TurnGuard` already counts what `max_turns` means there. The compression proxy of ADR 0055 stays
     behind it.
   - *MCP (4002).* One endpoint per granted server; the servers' headers stay in the runner
     (`loopback/mcp.py:75`). Every `tools/call` is judged by the guard rails before it leaves
     (`mcp.py:71`): for those servers the guard is no longer something a `curl` from the shell goes
     around. The run's own tool server keeps its address: it holds no credential the agent could
     take, and the API governs each of its calls whatever door they come through (ADR 0122 §2).
   - *Egress (4003).* The agent's proxy variables name the runner's CONNECT listener, which adds the
     run's identity upstream (`loopback/connect.py:86`). The agent no longer holds the egress token,
     which closes the first consequence of ADR 0115.

   The invariant, checked by a test: **no variable, no file and no volume of the agent's container
   holds a credential of the run** — run token, gateway key, git token, egress token, remote token, an
   MCP server's header. One case stays outside it and is journaled: a deployment without a gateway
   key, whose agent signs in with a credential of its own (`backends/base.py:123-128`).

### What each guarantee becomes

8. **Guarantee by guarantee.**

   | Guarantee | Today | With two containers |
   | :-- | :-- | :-- |
   | The agent never holds the run token (ADR 0115) | its environment is stripped; the runner's is one `/proc` read away | no process of the agent's container can name a process of the runner's |
   | Confinement (ADR 0094) | Landlock: writes, TCP ports, no `ptrace` of the runner; nothing under gVisor | writes and `ptrace` are the container's job, on every runtime. The launcher still applies `confine` to what it starts: the port rule is the one thing a container does not give, and it exists only where Landlock does |
   | Guard rails (`guardrails.py`) | judge ACP permission requests and the file methods; a shell command never asks | unchanged for the shell; no longer avoidable for the MCP servers behind the listener (§7); the file methods still write through `check_write` |
   | Turn and loop guard | counts ACP tool calls | unchanged |
   | Scope check (`scope.py`) | `git diff` and `checkout` in a repository the agent writes | the same commands in the runner's repository (§5) |
   | Evidence (ADR 0045) | the runner runs the checks in its own container | the runner asks for them and reads the exit code; they run in the agent's container. `measured` keeps its meaning and gains its honest qualifier: measured where the agent could prepare the ground. The pull request's CI is the measure taken outside |
   | Definition-of-done loop | the runner prompts again on a red check | unchanged |
   | Tool scripts (ADR 0122) | client and scripts under `$TMPDIR`, bridge on the tool server | client and scripts in the exchange directory, same bridge, same journal |
   | Kernel limits | one cgroup: an agent that exhausts memory kills the runner with it | memory and CPU are per container, so the runner survives to say `agent_exit: crashed`. The pid limit stays the pod's |

### Executors

9. **A declared capability and a refusal, never a quieter mode.** A deployment chooses
   `CHOREGOS_RUNNER_AGENT_ISOLATION`: `process` (today's shape) or `container`. An executor that can
   start the second container declares `separate_agent` among its `capabilities`, beside `queue` and
   `suspend`. Under `container`, an executor without it refuses the run when it starts, with the
   reason, and the API's readiness says so before any run — one comparison for both, as
   `runtime_class_refusal` is (`agent_sandbox.py:583`). A runner told `container` that finds no
   launcher ends with an exit code of its own, beside `CONFINEMENT_UNAVAILABLE`: it never starts the
   agent as its child instead. Every run journals `agent.isolation` before the agent starts, as it
   journals `agent.confinement`. This is ADR 0101's rule: a guarantee the platform does not apply is
   refused where it is asked for, not weakened where nobody looks.

   | Executor | Shape | What changes | `separate_agent` |
   | :-- | :-- | :-- | :-- |
   | `k8s_job` | a native sidecar beside `runner`, as for a hosted step (`_add_agent_container`) | the sidecar is the platform's image with the launcher; plain values, no `secretKeyRef`, no `envFrom` | yes on Kubernetes 1.29 or later, refused below (`native_sidecars_refusal`) |
   | `agent_sandbox` | the template's pod has both, warm | the reason it refuses a hosted sidecar (`agent_sandbox.py:141-146`) is a per-run image; the launcher's image is the pool's, so it can be in the template. The sealed assignment still goes to the runner alone; the executor must read the public key from the `runner` container's log by name (`agent_sandbox.py:374` names none today) | yes, once a cluster test has shown it ("Not decided from the code") |
   | `local_docker` | a second container in the runner's network namespace, two named volumes | what `_start_agent` does for a hosted step (`local_docker.py:125-159`) | yes |
   | `tekton` | a sidecar of the Task, beside the `choregos-tools` one the reference Task already has (inferred from that Task; not run) | the Task is copied into each project's namespace: the runner's own check (above) is what catches an old copy | not until its story |
   | `aca` | one container per job today (`aca.py:175-200`) | not known from the code: whether a job stops a second container when the first ends | no: `container` is refused |

10. **A code agent's image stops holding the runner.** ADR 0063 builds an operator-named image from the
    runner image and makes the runner its entry point: the code its author wrote runs beside every
    credential of the run. Under `container` that image is the agent's container, started by its
    launcher, and the runner is the platform's own image. The warm pool still refuses it
    (`agent_sandbox.py:150`) and the routing executor still sends it to `k8s_job`.
11. **Under gVisor the RuntimeClass is the pod's**, so one sentry holds both containers (ADR 0080,
    consequences). The boundary between them is then the sentry's separation of containers, not
    Landlock, which does not exist there; this is inferred from gVisor's model, not run here, and is
    the fifth item of "Not decided from the code". Of the three things S44-01b saw a `best_effort`
    child do under gVisor that it should not, two become impossible by construction — reading the
    runner's environment, writing beside the workspace into the runner's files — and one does not:
    connecting to an unlisted port, which stays the NetworkPolicy's job, as ADR 0094 §4 already says
    for hosts.

### Cost and migration

12. **One more container per run, no second image at first.** Both containers start from the runner
    image, which already holds the harnesses and the developer's tools: no second pull of an image of
    more than a gigabyte (`local_docker.py:115`). The agent's container takes the requests and limits
    the single container has today (`k8s_job.py:69-70`, `sandbox.yaml:93-95`), since the harness and
    the test suites are what use them; the runner's container needs its own, small and not yet
    measured. A namespace's quota counts both, and a LimitRange judges each container (`k8s_job.py:78`).
    A runner image without the harnesses and the compilers is a later story: it shrinks what the
    trusted container carries, and the boundary does not wait for it.
13. **The order.** `process` stays the default while the shape is proved: first on `local_docker`
    with the three harnesses the image installs, through the conformance suite of ADR 0002; then
    `k8s_job`, code agents first, because they are the least reviewed code beside the most
    credentials; then the warm pool. The default becomes `container` in a minor release, with an
    upgrade note, on every executor that declares the capability. On an executor that does not,
    `process` remains an explicit choice, journaled on every run, until its story is done. Removing
    the child-process path from the runner is a separate decision, taken when no shipped executor
    needs it.

## What this does not solve

- **A model that misbehaves within its rights.** The agent still writes the working tree: code that
  does harm once merged is the review's and the CI's to catch, as `SECURITY.md` says of bad code.
- **The tools it is granted.** A granted write is still a write; the grant, the operation policies and
  the approvals are where that is decided.
- **The network.** Both containers share the pod's network namespace. A connection the NetworkPolicy
  lets through is still open to the agent's shell; what changes is that it carries no credential.
- **Evidence the agent prepared.** A `conftest.py` decides what `pytest` reports. The checks moved out
  of the runner's container, not out of the agent's influence.
- **A job step** (ADR 0056) keeps its run token on purpose, in the runner's container
  (`runner.py:654-664`). When its command runs a file of a branch an agent wrote in an earlier step,
  that file runs with the token.
- **The launcher is not trusted.** It runs as the agent's user in the agent's container: what it
  reports is believed as far as that container is, and no further.
- **A fork bomb** in the agent's container still starves the runner of process ids: that limit is
  the pod's.
- **`workspace: read`** (ADR 0119) stays a scope checked after the fact where the pod exists before
  the run: a warm sandbox's tree is mounted writable.

## Consequences

- An agent's own commits are no longer pushed as commits: the runner commits the tree it finds, once
  per step, under its own identity. The history of a work branch becomes the runner's alone.
- A hosted agent that wrote the tree keeps its skipped checks (`runner.py:1045`): its container speaks
  Foundry's contract and has no launcher.
- The conformance suite gains a second shape to run, and stays the door ADR 0002 made it.
- The assumption of ADR 0084 §1 — a runner process the agent cannot rewrite — becomes true on every
  runtime, instead of where Landlock is.

## Alternatives rejected

- **A second user id in the same container.** The pod runs `runAsNonRoot`, as user 1000, with
  `allowPrivilegeEscalation: false` and every capability dropped (`k8s_job.py:269-312`,
  `sandbox.yaml:69-99`, `infra/policies/pod-security.yaml`). A process of user 1000 without
  `CAP_SETUID` cannot become another user, and `no_new_privs` disarms any set-uid helper. Two users in
  one container need a root start or that capability, and the policy refuses both. ADR 0115 had already
  noted that a second user would not have removed the token from the agent's own environment.
- **`hidepid` on `/proc`.** It hides the processes of *other* users; the two share one. Remounting
  `/proc` needs a capability the pod does not have.
- **The node's ptrace scope.** It is the node's setting, not the pod's; it restricts attaching, not
  the read of `/proc/<pid>/environ` (the probe above ran under scope 1); and it says nothing about
  `/app` being writable.
- **User namespaces.** A pod-level one separates the pod from the node, not two processes of the pod.
  A nested one, made by the runner for the agent, is refused by the seccomp profile the pods run under
  (ADR 0094 §7), and would leave every file of user 1000 writable (inferred: the files' owner is the
  same kernel user inside and outside it).
- **"Landlock is enough."** Not under gVisor (S44-01b), not on a kernel older than 5.13, not for the
  port rule before 6.7, and not for the checks, which it never wrapped.
- **The tokens in a sidecar, the agent left with the runner.** The runner is the judge, not only the
  keyholder: it decides what is allowed, what is in scope, what was measured and what is posted. An
  agent that can rewrite the judge has the keyholder push and post what the rewritten judge sends. A
  sidecar that also judged would be the runner, and the shape would be this one with the names swapped.
- **A copy of the tree, as for a hosted `write` agent** (`export_tree`, `import_tree`). A coding agent
  works with its repository's history, the definition-of-done loop runs the checks between turns, and
  the ACP file methods write into the tree: the copy would be made and brought back at every turn.
  §5 gives the runner's `.git` the same protection without copying the tree.
- **A third container for the checks.** It would protect the tools from what the agent left running,
  and nothing else: the code under test is the agent's.
- **`kubectl exec` or `docker exec` from the runner.** The run's pod has no service-account token
  (`k8s_job.py:266`, `sandbox.yaml:64`); giving it `pods/exec` would put a cluster credential in the
  one pod that runs a model's commands.
- **A Unix socket on a shared volume** for the launcher. No more trust than a loopback port — the
  agent's user owns either end — and a second kind of channel beside the one hosted steps already use;
  how it behaves between two containers of one gVisor sentry is not something this repository has run.
- **ACP over HTTP or WebSocket.** `agent-client-protocol` 0.12.1 ships both as an optional,
  experimental extra (`acp/http/__init__.py` in the wheel) that the repository does not install
  (`packages/agent-sdk/pyproject.toml:16`), and only code agents use that library. Whether a shipped
  harness can speak ACP on anything but its stdio is not known from the code; §3 makes the answer
  unnecessary.
- **Doing nothing.** Between 2026-10-21 and 2026-11-05 five changes each closed one path of the same
  sharing — the hosted checks (S41-18), `/proc` (ADR 0094), the proxy variables (ADR 0115), the
  inherited environment (S57-02), the hooks (#1345) — and their own "does not prove" lines name what
  was left.

## Not decided from the code

Each is an experiment of the plan, the first story among them:

- whether the three harnesses the image installs work with their model on the loopback listener: it
  serves five paths (`loopback/model.py:38`), and what each CLI asks beyond them is not in the code;
- whether the CONNECT listener carries a run's real traffic: it refuses plain HTTP
  (`loopback/connect.py:54`) and pipes every byte through the runner's process;
- whether the port rule can hold for a project's checks, which have never run confined;
- what the agent-sandbox operator makes of a native sidecar in a template: readiness, the pod's end,
  suspension with sub-path mounts. The pinned schema accepts the declaration
  (`tests/fixtures/agent_sandbox/crds-v1.0.6.json`); its behaviour needs a cluster;
- what one container of a gVisor pod sees of another: the probe of S44-01b, replayed across the two;
- what a Container Apps job does with a second container.
