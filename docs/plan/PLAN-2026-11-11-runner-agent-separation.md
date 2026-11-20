# Plan — 2026-11-11: the runner and the agent do not share a container (story S65-04)

Source: story S65-04, a design study. The runner is trusted and holds the run's credentials; the agent
is a coding-agent CLI driven by a model and is not; both run in one container under one user id. The
study asks whether a step an ACP agent carries can take the shape a hosted agent already has
(ADR 0080 §4-5), and what each guarantee of the runner then becomes.

Checked against `main` at `9f646fda4`. ADR: **0146**, proposed
([the decision](../adr/0146-the-runner-and-the-agent-do-not-share-a-container.md)). The stories below keep
the story's id — `S65-04a` to `S65-04n` — so that they stay under it. This page changes no code.

Status words: **exists** (in the code), **missing**, **broken** (exists and is wrong for this question).

Paths without a directory are under `packages/runner/src/tranibor_runner/`; the executors are under
`packages/adapters/src/tranibor_adapters/executor/`; `sandbox.yaml` is
`charts/choregos/charts/orchestrator/templates/sandbox.yaml`.

---

## 0. What the audit found

| What | Where | Today |
|:--|:--|:--|
| How the agent is started | `acp/client.py:156-164` | **Exists**: a child of the runner, pipes on its stdin and stdout. The runner's ACP client is its own code (`acp/client.py`, `acp/protocol.py`), not a library |
| The user both run as | `k8s_job.py:269-274`, `sandbox.yaml:69-73`, `docker/runner.Dockerfile:267` | 1000, in one container. `/app`, the runner's code, belongs to that user (`runner.Dockerfile:242`) |
| Where the run token is | `k8s_job.py:284`, `aca.py:185`, `local_docker.py:80`, `templates/github-tekton-argo-k8s/tekton/agent-stage.yaml:34`; `assignment.py` | In the runner's environment on four executors; in memory under `agent_sandbox` |
| What keeps it from the agent | `config.py:26-43` (ADR 0115, S57-02) | **Exists** for the agent's own environment. The runner's `/proc/<pid>/environ` is left to the confinement |
| The confinement | `confine.py`, applied at `runner.py:713-739` (ADR 0094) | **Exists** on a kernel with Landlock; **missing** under gVisor (STATUS, S44-01b: the child does all five things probed) |
| The project's checks | `dod.py:138-177`, started by `workspace.py:189-227` | **Broken**: a shell of the runner's container, not wrapped by `confine`, running the code the agent wrote. `runner.py:1034-1059` skips them for a hosted agent that wrote the tree, for that reason |
| The runner's git | `workspace.py:72-79`, `:240-267`, `:485-502` | In the tree's own `.git`, which the agent writes; protected by a list of overrides (#1345); the git token on the command line of the push |
| The gateway key | `runner.py:365-369`, `backends/base.py:122-146` | In the agent's environment, by design |
| The egress token | `runner.py:246-250`, `egress.py:24` | In the proxy variables the agent inherits (ADR 0115, first consequence) |
| The MCP servers' headers | `backends/base.py:76-104`; `apps/orchestrator/src/tranibor_orchestrator/activities/stage_tools.py:25` | Sent to the agent in `session/new` |
| The run's tool server | `local_tools.py:53-87`, `runner.py:553-585` | **Exists**: served by the runner on loopback with the runner's client; the agent gets no credential and the API governs each call (ADR 0122 §2) |
| The ACP file methods | `acp/client.py:463-534`; `terminal: false` at `:208` | Served by the runner on its own file system, inside the workspace or the script directory |
| What the runner reads in the tree | `result.py:57`, `outputs.py:47-82`, `job.py:82`, `runner.py:1430` | Plain `Path` calls, written when the tree's writer shared the container. Opening from a descriptor with `O_NOFOLLOW` **exists** in `hosted_workspace.py:45`, `:185-240` only |
| The runner's own files in the tree | `runner.py:270-272`, `:1390`, `workspace.py:23` | The transcript, the saved session id and the final result are under `.choregos/` |
| The agent's home, the tool client, the scripts | `workspace.py:615-640`, `tool_scripts.py:145-152` | Under the container's temporary directory, which both share |
| Hosted agents | `k8s_job.py:355-400`, `local_docker.py:125-159`, `packages/core/src/tranibor_core/hosted.py`, `loopback/` | **Exists**: a container beside the runner, no secret, three loopback listeners, a copy of the tree |
| The model listener | `loopback/model.py:38`, `:88`, `:94`, `:121` | **Exists** for hosted steps: five paths, the run's key and model, every request counted as a turn |
| The MCP listener | `loopback/mcp.py:44-75` | **Exists**: one endpoint per granted server that has a URL, `tools/call` judged, server streams refused |
| The CONNECT listener | `loopback/connect.py:34-86` | **Exists**: TLS tunnels only, the run's identity added upstream |
| A second container, per executor | `agent_sandbox.py:141-146`, `tekton.py:104-109`, `aca.py:153-158`, `routing.py:38-42` | `k8s_job` and `local_docker` run one; the three others refuse; the routing executor sends such a step to `k8s_job` |
| What an executor declares | `k8s_job.py:61`, `agent_sandbox.py:94`, `tekton.py:84`, `aca.py:117`, `local_docker.py:31`; read by `apps/orchestrator/src/tranibor_orchestrator/activities/execution.py:188-198` | **Exists**: `capabilities`, where `separate_agent` goes |
| The sandbox template | `sandbox.yaml:52-112`, `infra/policies/sandbox-template.yaml`, `tests/fixtures/agent_sandbox/crds-v1.0.6.json` | One container. The pinned schema accepts an init container with `restartPolicy` and mounts with `subPath`; the admission policy already judges init containers |
| The assignment's key | `agent_sandbox.py:358-426`, `assignment.py` | Read from the pod's log without naming a container (`:374`, `:569`) |
| A code agent | ADR 0063 §6, `backends/code.py` | The operator's image is built from the runner image and the runner is its entry point |
| The ACP library | `packages/agent-sdk/pyproject.toml:16` | `agent-client-protocol` 0.12.1, used by code agents only. Its wheel has stdio over any stream pair (`acp/core.py`) and, behind an extra the repository does not install, an HTTP and a WebSocket transport marked experimental (`acp/http/__init__.py`) |
| The harness commands | `backends/claude_code.py:34`, `backends/others.py:25`, `:59`, `:90`, `:163`, `:248`, `backends/versions.lock` | Six commands that speak on their stdio; the image installs three of them |

Three things were run for the study, none of them in the repository:

- **A child reads its parent's environment.** Linux 6.18, `kernel.yama.ptrace_scope` 1: a Python child
  started with a variable removed from its environment finds it in `/proc/<parent>/environ`. The node's
  ptrace scope does not cover that read.
- **git with its directory outside the tree.** git 2.53.0: a repository copied out of the tree before
  anything is planted, then `core.fsmonitor`, a `filter` driver named by `.gitattributes` and four
  hooks written into the tree's own `.git`. `git --git-dir=<copy> --work-tree=<tree>` ran `status`,
  `add -A` and `commit` with none of them called; `git status` in the tree called one. The commit held
  the changed files and no `.git` path.
- **The pinned sandbox schema**, read with a script: `SandboxTemplate.spec.podTemplate.spec.initContainers[]`
  has `restartPolicy` and `volumeMounts[].subPath`.

Conclusion: the machinery of the second container is there, built for hosted agents. What an ACP step
lacks is a way to start the harness on the other side (a launcher), a repository the agent cannot write
for the runner's git, a place other than the runner's container for the checks, and the runner's care
with links when it reads the tree. None of these needs a harness to change.

## 1. What could not be decided from the code

| Question | Why the code cannot say | The experiment | Story |
|:--|:--|:--|:--|
| Do the three installed harnesses work with their model on the loopback listener? | The listener serves five paths; what each CLI calls beyond them is in the CLI, not here | The conformance suite of ADR 0002 with the harness behind a launcher and the three listeners, on `local_docker` | a |
| Does the CONNECT listener carry a run's real traffic? | It refuses plain HTTP and pipes every byte through the runner's process; a run's installs were never sent through it | The same run, with a dependency install; time and failures recorded | a |
| Can the port rule hold for a project's checks? | The checks have never run confined; a test suite that opens local sockets may not survive it | The checks of two real repositories under the launcher's `confine`, on a kernel with Landlock ABI 4 | a |
| What does the runner's container need for itself? | It has never run without the agent beside it | Peak memory and CPU of the runner's container over the same runs | a |
| What does the agent-sandbox operator make of a native sidecar in a template? | The schema accepts it; readiness, the pod's end and suspension are the controller's behaviour | `tests/cluster` on a cluster with the operator | i |
| What does one container of a gVisor pod see of another? | Nothing here runs two containers under gVisor and looks | The probe of S44-01b across the two containers, on runc and under gVisor | o |
| Does a Container Apps job stop a second container when the first ends? | The executor declares one container | A live job with two | l |

Whether a harness can speak ACP on anything but its own stdio is **not** on this list: the launcher
starts it with pipes, so the answer changes nothing.

## 2. Stories and order

| Story | Wave | Size | Title | Depends on |
|:--|:-:|:-:|:--|:--|
| S65-04a | 1 | M | Measure: the shape on `local_docker`, with the three installed harnesses | — |
| S65-04b | 1 | M | The runner follows no link in the tree | — |
| S65-04c | 1 | M | The runner's repository is not in the tree | — |
| S65-04d | 1 | S | The setting, the capability and the refusal | — |
| S65-04e | 2 | L | The launcher, and `AcpClient` over a stream | d |
| S65-04f | 3 | M | No credential in the agent's container | a, e |
| S65-04g | 3 | M | The checks run in the agent's container | a, e |
| S65-04h | 3 | M | `local_docker` and `k8s_job` start the agent's container | c, d, e |
| S65-04i | 4 | L | A warm sandbox has two containers | h |
| S65-04o | 4 | M | What the agent's container reaches, on runc and under gVisor (planned as S65-04j) | h |
| S65-04k | 4 | M | A code agent's image is the agent's container | h |
| S65-04l | 5 | M | `tekton` and `aca` say what they can | h |
| S65-04m | 5 | S | `container` is the default | f, g, i, o, k |
| S65-04n | 6 | M | A runner image without the harnesses | m |

```
wave 1  a measure · b links · c repository · d setting        (independent; b and c hold today)
wave 2  e launcher                                            (after d)
wave 3  f credentials (a, e) · g checks (a, e) · h two executors (c, d, e)
wave 4  i warm pool · o probes · k code agents                (after h)
wave 5  l tekton, aca (h) · m default (f, g, i, o, k)
wave 6  n runner image                                        (after m)
```

Stories b and c are worth doing whatever becomes of the rest: each closes something in today's shape.
If story a finds a harness that cannot work behind the listeners, f stops for that harness and the ADR
is amended before wave 3; the boundary of waves 2 and 4 does not depend on it.

### S65-04a · measure(runner): the shape on `local_docker`, with the three installed harnesses

- A launcher good enough to start a harness in a second container, and a conformance run in that
  shape for `claude-code`, `gemini-cli` and `opencode`, with their model on 4001, their MCP servers on
  4002 and their proxy on 4003. What is built here may be thrown away.
- Recorded in STATUS: per harness, the paths and headers it asked of the model listener; a dependency
  install through the CONNECT listener (time, failures); the checks of two repositories under the port
  rule; the runner's container's peak memory and CPU.
- **Accepted when**: `tests/conformance/backends/test_acp_conformance.py`, run in the two-container
  shape, passes its seven checks for the three harnesses, or the STATUS row names each harness and
  check that fails and why.

#### Measured (S65-04a, 2026-11-19)

`tests/live/test_agent_shape_live.py` (marker `live`) makes the pod with Docker: the runner's container
on an `--internal` network whose only way out is an egress proxy container standing in for the pod's
(`agent_shape/egress_proxy.py`, it logs every tunnel); the agent's container in the runner's network
namespace, uid 1001 group 1000, read-only root, its own `/tmp`, no capability, no secret, `choregos-runner
launcher` as its command; `/workspace` and `/exchange` shared, empty, setgid group 1000, as the
SandboxTemplate gives them (S65-04i). The step runs through `Runner.execute` with
`agent_isolation=container` (`agent_shape/runner_side.py`): fix `add()` in a small Python repository, read a
code word from an MCP server that wants a bearer header, write it to a file; the project's checks are
`make test`, a dependency install through the proxy variables (`pip install six`, `npm pack is-number`)
and a probe that looks, by SHA-256 digest, for the gateway key, the egress token, the MCP header and the
run token in every `/proc/*/environ` and `cmdline` it can read and every small file of `/workspace`,
`/exchange` and `/tmp`. Model: `choregos-claude-haiku` on the dev LiteLLM, key `tranibor-live-tests`.
Image: 0.27.15 with the two image fixes below applied on top, the checkout's `packages/` mounted over
the image's; Linux 6.18 (WSL2), Landlock ABI 7, `confinement: best_effort`.

| | claude-agent-acp 0.85.1 | opencode 1.18.31 | gemini-cli 0.60.0 |
|:--|:--|:--|:--|
| Started by the launcher, in the agent's container | yes | yes, after #1977 (exited before `initialize`, then `session/new` refused) | yes |
| What it asked of 4001 | `POST /v1/messages?beta` ×9, `POST /v1/messages/count_tokens?beta` ×20 (both 404 before #1964) | `POST /chat/completions` ×9 | `POST /v1beta/models/<m>:streamGenerateContent?alt=sse` ×9-26 |
| Headers it sent there (names) | `x-api-key`, `authorization`, `anthropic-version`, `anthropic-beta`, `anthropic-dangerous-direct-browser-access`, `x-app`, `x-claude-code-session-id`, `x-stainless-*` | `authorization`, `x-session-affinity`, `x-session-id` | `x-goog-api-key`, `x-goog-api-client`, `x-gemini-api-privileged-user-id` |
| Key in its environment | the loopback key only (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`) | the loopback key only (`OPENAI_API_KEY`) | the loopback key only (`GEMINI_API_KEY`, `OPENAI_API_KEY`) |
| Probe: the four credentials in its container | none, 8 processes all uid 1001, no runner process visible | none, 7 processes | none, 8 processes |
| MCP through 4002, header added by the runner | yes: `server/discover`, `initialize`, `tools/list`, `tools/call` | yes | yes after #1991 (no request at all before) |
| Network: direct / through 4003 | direct refused (no route, no DNS); `https://pypi.org` 200 in 0.15 s | same | same |
| What it tried to reach on its own | `api.anthropic.com` ×3, refused (#1987) | nothing | `play.googleapis.com` ×13-41, refused (#1987) |
| Dependency install through 4003 | pip 0.99 s, npm 0.77 s, no failure | pip 1.0 s, npm 0.86 s | pip 1.05 s, npm 0.95 s |
| The project's checks, in the agent's container | 3 of 3 pass | 3 of 3 pass | 3 of 3 pass (the file was fixed) |
| Tool calls (`max_turns` 25) | 9 | 9 | 2-3 |
| Outcome | `done`, 20 s, 0.004 USD at the gateway | `done`, 25 s | `invalid_result`: the gateway refused the turns (#1990) |
| Runner container: peak memory, CPU | 80 MiB, 2.1 s | 82 MiB, 2.3 s | 77-89 MiB, 2.7-4.1 s |
| Agent container: peak memory, CPU | 298 MiB, 7.0 s | 693 MiB, 19.0 s | 394-406 MiB, 7-8 s |

What broke and was fixed: #1964 (the model listener forwarded `messages` to the gateway's root, where
LiteLLM serves it only under `/v1`, and did not serve `count_tokens`; the image's `/workspace` held the
build's caches, so a Docker run's fresh tree was taken for a replay's and nothing was checked out),
#1977 (opencode wrote into its read-only config root at start; its ACP layer connects to its own server
on a random loopback port, which the port rule refused), #1991 (gemini-cli read no MCP server from an
untrusted workspace). Findings: #1986 (a `session/new` error escapes `Runner.execute`; a dead agent is
seen only at the 60 s `initialize` timeout), #1987 (harness telemetry through 4003), #1990 (gemini-cli on
a Claude model through LiteLLM's Gemini adapter).
Since then `gemini-cli` is declared with Gemini models only (#1990: LiteLLM's adapter names every tool call
`call_<function name>` and reads no id from the body, so no listener-side id can help).
An agent that fails before its first prompt now ends the run with a failed result naming how
(`agent_exited`, `agent_session_refused`, `agent_unreachable`), when it fails rather than at the `initialize` timeout (#1986).

Not measured: the conformance suite file itself in this shape (its seven checks are read from the
step's journal instead: `initialize`, MCP, the trivial prompt, the result, `max_turns` and the cost at
the gateway; **a denied write was not attempted**, so `permission_denied` is not measured); the checks of
two real repositories under the port rule (the checks run unconfined in the agent's container, S65-04g);
a kubelet, the agent-sandbox operator, gVisor; an image built by the CI with the fixes (they were
applied on top of 0.27.15). A last run of the three, meant to give one set of numbers from one image,
was cut short by the dev gateway (502 during a LiteLLM rollout, then `max_parallel_requests` 4 held at
0): the numbers above come from the last good run of each harness.

### S65-04b · fix(runner): the runner follows no link in the tree

- One helper, the discipline of `hosted_workspace.py` (a descriptor per directory, `O_NOFOLLOW`),
  used by every reader and writer the runner has in the tree; the story starts by listing them.
- The transcript and the saved session id leave the tree for a directory of the runner's own.
- **Accepted when**: for each reader on that list, a test puts a symbolic link where the runner reads,
  and the runner refuses it and journals the refusal; each test is red with that reader's guard
  removed.

### S65-04c · fix(runner): the runner's repository is not in the tree

- `Workspace` keeps its git directory outside the work tree (`--git-dir`, `--work-tree`) for every
  command it runs; the tree keeps a `.git` for the agent, prepared before the agent starts, with no
  credential, never read again. The overrides of #1345 stay.
- What changes for a reader of the branch: the agent's own commits are no longer pushed as commits.
- **Accepted when**: a run whose tree's `.git` holds a `core.fsmonitor` program, a `filter` driver and
  hooks that each write a marker goes through prepare, the scope check, the commit and a push to a
  local remote with no marker written, the change pushed and no `.git` path in the commit;
  `packages/runner/tests/test_runner_git_hooks.py` stays green.

### S65-04d · contract(core,adapters): the setting, the capability and the refusal

- `CHOREGOS_RUNNER_AGENT_ISOLATION` (`process`, the default, or `container`), the chart's value, the
  `separate_agent` capability, the runner's exit code for "no launcher", the `agent.isolation` event,
  and the API's readiness line. No executor declares the capability yet.
- **Done** (2026-11-19): the chart's value is `global.runner.agentIsolation` (the API, the workers and the
  warm pool's template read one value); `agent_sandbox` declares the capability when its template has the
  agent's container; the routing executor sends a run to the executor that can isolate it.
- **Accepted when**: under `container`, `start` on an executor without the capability raises a
  `ConfigurationError` that names the executor and sends nothing to its backend; readiness says the
  same sentence; a runner told `container` with nothing on the launcher's port exits with the new code
  and has started no process.

### S65-04e · feat(runner): the launcher, and `AcpClient` over a stream

- `choregos-runner launcher`: standard library only; one loopback port; per connection, one process
  with its command, environment, working directory and home files, its three streams and its exit
  code; `confine` applied to what it starts; a clean exit on SIGTERM.
- `AcpClient` takes a transport: a child process (today) or a launcher connection. The 64 MiB message
  bound and the stderr tail hold on both.
- **Accepted when**: the runner's ACP tests pass with the fake agent started through a launcher over
  loopback, with the journal a child process gives; a launcher killed during a turn ends the run with
  `agent_exit: crashed` and a posted result; under `container`, a runner changed to fall back to a
  child process turns a test red.

### S65-04f · feat(runner): no credential in the agent's container

- For an ACP step under `container`: the model listener without the turn count and with the paths
  story a recorded, the MCP listener for every granted server but the run's own tool server, the
  CONNECT listener in the proxy variables; the backends' `model_env` gives the loopback address and
  the constant key.
- **Accepted when**: the digest check of `packages/runner/tests/test_agent_holds_no_run_token.py`,
  extended to the gateway key, the egress token, the git token, the remote token and each MCP header,
  finds none of them in what the launcher receives; a fake gateway sees the run's key on a request the
  agent sent with the loopback key; a step with `max_turns: 5` gets its thirtieth model request
  forwarded.

### S65-04g · feat(runner): the checks run in the agent's container

- `run_checks` and the fact commands go through the launcher under `container`; the report, the
  evidence and the repair prompt are built by the runner from what comes back, as today.
- **Accepted when**: on `local_docker`, a check command that prints its environment and every
  `/proc/*/cmdline` shows no credential of the run and no process of the runner; the loop journals the
  same `dod.retry` events as under `process`; under `container`, a runner changed to start a project
  command itself turns a test red.

### S65-04h · feat(adapters): `local_docker` and `k8s_job` start the agent's container

- The Job gets a native sidecar from the platform's image with the launcher as its command; Docker
  gets a second container in the runner's network namespace. The tree and the exchange directory are
  mounted in both, the runner's directory in `runner` only, the agent's home in `agent` only. Both
  executors declare `separate_agent`.
- **Done** (2026-11-19, STATUS S65-04h): both executors, the kind proof of the Job, and the runner's own
  requests (§12: 250m/512Mi, limits 1/2Gi, measured). Beside a hosted agent the launcher's container is
  named `launcher`.
- **Accepted when**: the Job's `agent` container has `restartPolicy: Always`, no `secretKeyRef`, no
  `envFrom` and only those mounts; a cluster older than 1.29 is refused before anything is created;
  `docker run` for the agent names no secret variable; on a kind cluster the Job completes and the
  sidecar exits.

### S65-04i · feat(charts,adapters): a warm sandbox has two containers

- The SandboxTemplate gains the sidecar; the run's volume claim is mounted by sub-path; the executor
  reads the public key and the logs from the `runner` container by name; `agent_sandbox` declares the
  capability.
- **Accepted when**: the rendered template validates against `tests/fixtures/agent_sandbox/crds-v1.0.6.json`
  and passes the admission policy; the executor seals to the key of the `runner` container's log and
  ignores a key line of the other container; on a cluster with the operator a run completes, and a
  suspended then resumed run finds its tree, the runner's repository and the agent's home.

### S65-04o · test(cluster): what the agent's container reaches, on runc and under gVisor

Planned as S65-04j; that id was taken in STATUS by an unrelated story (an agent's project memory over
MCP, #1882), so the cluster proof carries **S65-04o**.


- The probe of S44-01b, run from the agent's container of a real pod: the runner's environment, the
  runner's files, the listed and the unlisted port.
- **Accepted when**: no `/proc/<pid>/environ` readable from the agent's container holds the run token,
  and no path of the runner's container outside the shared volumes can be opened, on runc and under the
  `gvisor` RuntimeClass; the STATUS row records both runtimes and what the unlisted port answered.

### S65-04k · feat(runner,adapters): a code agent's image is the agent's container

- For the `code` backend under `container`, the Job's `runner` is the platform's image and its `agent`
  the operator's, started by the launcher that image inherits. `docs/code-agents.md` says what such an
  image must hold.
- **Accepted when**: the Job of a code agent names the platform's image for `runner` and the
  operator's for `agent`, and `make conformance-agent` passes in that shape.
- **Done** (2026-11-19, STATUS S65-04k), with one gap: the conformance suite runs every code agent behind a
  launcher too, and the plain example passes from its source; with an image (`make conformance-agent
  IMAGE=...`) it needs `--network=host` to reach the fake gateway, which Docker Desktop does not give the
  WSL host, so the image shape was not run here (the launcher was seen starting in the image as uid 1001
  under a read-only root).

### S65-04l · feat(adapters): `tekton` and `aca` say what they can

- The reference Task gains an `agent` sidecar and the executor declares the capability; for `aca`, a
  live job with two containers decides.
- **Accepted when**: `tests/conformance/executors/test_capabilities.py` shows `tekton` declaring
  `separate_agent` with a Task that carries the sidecar; `aca` either declares it with a live run
  recorded in STATUS, or refuses `container` with a reason that names it — one test either way.
- **Done** (2026-11-19, STATUS S65-04l), the refusal for both: neither declares the capability (the two
  were frozen by ADR 0180 when the work started); each refuses `container` naming itself and the reason.
  A Task sidecar for `tekton` or a live two-container job for `aca` stays open for whoever needs them.

### S65-04m · chore(release): `container` is the default

- The chart's default; the upgrade note; `process` stays a choice on an executor without the
  capability, journaled on every run.
- **Accepted when**: the default values render the two-container template and Job;
  `tests/docs/test_upgrade_notes.py` names the setting; a run under `process` journals
  `agent.isolation` with that word.
- **Done** (2026-11-20, STATUS S65-04m): the chart's default from 0.28; the code's default, read when nothing sets
  the variable (Compose, the demo, a runner its executor says nothing to), stays `process`. The upgrade note is
  under "Upgrading to 0.28" and its test is `tests/docs/test_upgrade_notes_agent_isolation.py`.

### S65-04n · build(docker): a runner image without the harnesses

- Two images: the runner's (Python, git, the runner) and the agent's (today's, with the launcher).
- **Accepted when**: a packaging test finds no `node`, no harness binary and no compiler in the
  runner's image, and the conformance suite passes with the two.

## 3. What every story must keep true

Each is a sentence of ADR 0146 with the story whose test holds it.

| Invariant | Held by |
|:--|:--|
| No variable, file or volume of the agent's container holds a credential of the run | f, h, i |
| The runner executes no program stored where the agent can write | c, g |
| The runner follows no link when it reads or writes a shared volume | b |
| A deployment that asks for `container` never gets `process`: the executor refuses, the runner exits | d, e |
| From the agent's container, the runner's environment and files cannot be read, on runc and under gVisor | o |

Shared by every story: `make ci` green; a STATUS row saying what the pull request proves and does not
prove; a guard is delivered only with the test that fails without it.

## 4. The backlog

`tests/docs/test_plan_stories_in_backlog.py` reads `PLAN-2026-10-27.md` and the streams S45 to S47 only,
and `08-backlog.yaml` holds no story of a stream after S58. These stories are therefore not added to
it; this page and their STATUS rows are their record.
