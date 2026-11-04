# 2. Orchestrator, agents, runner

## 2.1 Temporal orchestrator (`apps/orchestrator`)

### Workflows

| Workflow | ID | Role |
| :-- | :-- | :-- |
| `WorkflowInterpreter` | `wi-<project>-<tracker_key>` | Runs the pinned DSL for a ticket |
| `ReleaseTrain` | `train-<project>-<env>` | Serialises the deployments of an environment (section 5) |
| `FindingsTriage` | `findings-<project>` | Deduplicates, classifies, creates the tickets (section 5) |
| `ProjectProvisioning` | `prov-<project>` | Runs the template's steps (section 3) |
| `MemoryIngestion` | `mem-<project>` | Scheduled tracker/SCM/CD import → Ecphoria (section 4) |
| `EvalMatrix` | `evals-<project>` | Nightly backend × model evals (section 4) |

Task queues: `orchestrator` (workflows + light activities), `executor` (creation/tracking of runs), `tracker` (external API calls, rate-limited), `memory`. Workers: `apps/orchestrator/worker.py --queues …`; deployed with 2+ replicas per queue (section 6).

The table that says **which activity goes on which queue** lives in `choregos_orchestrator.repartition`, and the workflows schedule each activity on the declared queue through `workflows.planification.executer_activite`. This detail is not a detail: until 2026-09-27, the table was only read by the worker (to know what it registers) and never by the workflows (to know where to schedule). `workflow.execute_activity` without a `task_queue` schedules on the *workflow's* queue — so everything ran on `orchestrator`, the three specialised workers received nothing, and a network policy written from the declared distribution failed. The four histories in `tests/replay/` carry the trace: 83 activities scheduled, **all** on `orchestrator`.

### `WorkflowInterpreter` — algorithm

```python
@workflow.defn
class WorkflowInterpreter:
    def __init__(self):
        self.inbox: deque[Signal] = deque()
        self.attempts: Counter = Counter()
        self.state: str = ""
        self.cost_usd = 0.0
        self.paused = False
        self.stopped = False

    # signals
    @workflow.signal
    def human_decision(self, d: HumanDecision):
        self.inbox.append(d)

    @workflow.signal
    def inbound(self, e: InboundEvent):
        self.inbox.append(e)  # board_moved, ci_result, pr_*, deploy_result

    @workflow.signal
    def finding(self, f: Finding):
        self.pending_findings.append(f)

    @workflow.signal
    def control(self, c: Control): ...  # pause | resume | stop | migrate(workflow_def_id)
    @workflow.query
    def status(self) -> Status:
        return Status(self.state, self.cost_usd, self.attempts, self.current_run)

    @workflow.run
    async def run(
        self,
        wi: WorkItem,
        wf: WorkflowDef,
        project: ProjectConfig,
        policy: Policy,
        resume_from: str | None = None,
    ):
        self.state = resume_from or wf.initial_state
        await self.act(mirror_state, wi, self.state)
        while not wf.states[self.state].terminal and not self.stopped:
            await workflow.wait_condition(lambda: not self.paused)
            t = wf.select_transition(self.state, self.last_outcome)
            if t is None:
                self.state = await self.wait_external_transition(wf)
                continue  # human waiting state (board)
            if t.via == "release_train":
                await self.act(signal_train, project, t.train.env, wi, self.last_pr)
                deploy = await self.wait_for(DeployResult, timeout=t.timeout)
                self.state = t.to if deploy.ok else wf.on_rollback_state
            elif t.actor.type == "agent":
                res = await self.run_stage(wi, t, project, policy)
                self.state = self.after_stage(t, res, wf)
            elif t.actor.type == "human":
                dec = await self.wait_human(wi, t, project, policy)  # creates human_request, SLA, reminders
                self.state = t.to if dec.approved else (t.on_reject or self.state)
            else:  # system
                ok = await self.wait_gates(t.gates, wi, timeout=t.timeout)
                self.state = t.to if ok else self.retry_or_escalate(t, wf)
            await self.act(mirror_state, wi, self.state)
            if workflow.info().get_current_history_length() > 20_000:
                workflow.continue_as_new(wi, wf, project, policy, resume_from=self.state)
        await self.act(close_out, wi, self.state, self.cost_usd)
        return Outcome(self.state, self.cost_usd)

    async def run_stage(self, wi, t, project, policy) -> StageResult:
        attempt = self.attempts[t.id]
        self.attempts[t.id] += 1
        budget = policy.budget_for(t.actor.role, wi.size)
        model = await self.act(resolve_model, project, t.actor.model, wi.size)  # profiles → litellm_model
        key = await self.act(mint_gateway_key, project, wi, t, attempt, budget, model)
        ctx = await self.act(build_context_pack, project, wi, t)  # Ecphoria + linked tickets + CI logs
        spec = StageInput.build(wi, t, project, model, key, budget, ctx, attempt)
        ref = await self.act(start_run, spec)  # Executor.start → Tekton PipelineRun
        res = await self.act(
            await_run, ref, heartbeat=60, timeout=budget.max_minutes + 10
        )  # heartbeat, cancel on stop
        spend = await self.act(collect_spend, key)
        await self.act(revoke_key, key)
        self.cost_usd += spend.cost_usd
        await self.act(record_run, wi, t, attempt, res, spend)
        await self.act(update_ticket_status_comment, wi)
        for f in res.findings:
            await self.act(emit_finding, project, wi, ref, f)
        for q in res.scope_changes_requested:
            await self.handle_scope_change(wi, t, q, policy)
        if self.cost_usd > policy.budget_ticket(wi.size):
            res.status = "needs_human"
            res.summary += " (ticket budget exceeded)"
        return res

    def after_stage(self, t, res, wf) -> str:
        if res.status == "done":
            gates_ok = await_gates_sync(
                t.gates
            )  # evaluated by activity; asynchronous gates (CI) → wait for a signal
            return t.to if gates_ok else self.retry_or_escalate(t, wf)
        if res.status == "needs_human":
            return wf.defaults.on_question
        return self.retry_or_escalate(t, wf)  # failed | blocked

    def retry_or_escalate(self, t, wf) -> str:
        r = t.on_fail
        return r.to if self.attempts[t.id] < r.max_attempts else r.escalate_to
```

Activities (`apps/orchestrator/activities/`): `mirror_state`, `resolve_model`, `mint_gateway_key`, `collect_spend`, `revoke_key`, `build_context_pack`, `start_run`, `await_run` (with heartbeat and cancellation), `record_run`, `update_ticket_status_comment`, `emit_finding`, `create_human_request`, `notify`, `evaluate_gate`, `signal_train`, `close_out`, `write_run_lesson` (memory). All idempotent by `(run_id|work_item_id, step)`. Temporal retries: network 5× exponential; business errors (`ApplicationError(non_retryable=True)`).

Human wait: `create_human_request` posts a structured comment on the ticket (and a notification), then `wait_for(HumanDecision)` with timers: reminder at `sla_hours`, then escalation to `escalate_to`. A decision can arrive through the board (card moved), through a `/choregos approve|reject|answer …` comment (parsed by the webhook), through the front end, or through the CLI.

Asynchronous gates: `ci_green` and `review_approved` wait for `InboundEvent`s (`scm.check.completed`, `scm.pr.review_submitted`); timeout → `retry_or_escalate`. `scope_respected`, `evidence_present`, `diff_size_max`, `no_secrets` are synchronous (`evaluate_gate` activity that calls `ScmAdapter.compare`).

Migration: signal `control(migrate, workflow_def_id)` → the interpreter checks that the current state exists in the new definition (otherwise a mapping is provided) then `continue_as_new` with the new definition.

## 2.2 The runner (`packages/runner`) — headless ACP client

Image `ghcr.io/vargafoundation/choregos-runner:<version>`: Debian slim + Python 3.12 + `git`, `gh`, `glab`, `jq`, `ripgrep`, the usual test tools (node 22, pnpm, uv, go, java 21 in image variants), scanners (Semgrep, Trivy, gitleaks), **preinstalled ACP agents** (`claude-agent-acp`, `codex-acp`, Gemini CLI, Goose, OpenCode — OpenHands removed, [ADR 0011](../adr/0011-openhands-removed.md)) at versions pinned in `packages/runner/backends/versions.lock`.

### Algorithm

```
1. input  ← GET /internal/runs/{id}/input (run_token)            ; if the result is already posted → exit 0 (idempotence)
2. clone  ← git clone --depth N repo ; checkout work_branch (created from base if absent) ; git config user = choregos-bot
3. ctx    ← download context_pack ; write .choregos/context.md ; write .choregos/task.md (ticket + spec + output instructions)
4. tools  ← start the choregos-tools MCP sidecar (localhost:7777) with run_token ; write the backend's MCP config
5. agent  ← backend.launch_spec(ctx) : write config files (model, MCP), export model_env ; spawn process ; ACP initialize
6. sess   ← session/new(cwd=/workspace, mcpServers=[choregos, memory, github-readonly])
7. prompt ← session/prompt(rendered playbook + task.md)           ; event loop:
      - session/update → log (batch POST /internal/runs/{id}/events), count turns/tools
      - session/request_permission → PolicyEngine.decide(op) :
            write outside allowed_paths → deny + message "report_finding / request_scope_change"
            command in deny_commands or dangerous pattern → deny
            network outside the allowlist → deny (the sandbox blocks it too)
            otherwise allow (logged)
      - max_turns / max_minutes exceeded → cancel session → status failed(reason=limit)
8. dod    ← outer loop (max policy.dod_iterations, default 3) :
      a. run `make test` / the repository's command ; lint ; typecheck
      b. on failure → session/prompt("The checks fail: <truncated output>. Fix it.") ; back to 7
9. scope  ← git diff --name-only base..HEAD ; files outside allowed_paths → revert those files + reminder prompt (once) ; if any remain → status blocked(reason=scope)
10. result← read .choregos/result.json ; validate schema ; invalid → repair prompt (twice) ; complete evidence/artifacts/diagnostics
11. publish← conventional commit (if diff), push ; upload transcript (JSONL of the ACP events) + reports to the object store
12. POST /internal/runs/{id}/result ; exit 0
```

Standardised exits: code 0 (result posted, whatever the `status`), 10 (input not found), 20 (clone impossible), 30 (agent unreachable — broken backend), 40 (invalid result after repairs). Any other code = crash → the `await_run` activity replays once with the same `run_id`.

### Backends (`packages/runner/backends/`)

| Backend | `launch_spec` | `model_env` | Notes |
| :-- | :-- | :-- | :-- |
| ~~`openhands`~~ **removed** | no command-line ACP agent (0.59: `serve`/`cli`; 1.x: HTTP `agent-server`) — [ADR 0011](../adr/0011-openhands-removed.md); the default is `claude-code` | — | — |
| `claude-code` | `claude-agent-acp` (`@agentclientprotocol/claude-agent-acp`, pinned in `versions.lock`; the image keeps a `claude-code-acp` link for stages that name the old Zed package, #436); `CLAUDE.md` → link to `AGENTS.md`; project `settings.json` with fallback hooks | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` | Claude models only (project validation) |
| `codex` | `codex-acp`; native `AGENTS.md` | `OPENAI_BASE_URL`, `OPENAI_API_KEY`, model via config | |
| `gemini-cli` | `gemini --acp` (`--experimental-acp` deprecated since 0.39) | `GOOGLE_GEMINI_BASE_URL` (via LiteLLM), key | |
| `goose` | `goose acp` | `openai` provider pointed at LiteLLM | |
| `opencode` | `opencode acp` or `opencode serve` + SDK | `opencode.json` `openai-compatible` provider → LiteLLM | Moving API: conformance suite mandatory |
| `copilot-cli` | `copilot --acp` | per GitHub docs | Optional |

Conformance suite (`tests/conformance/backends/`) run in CI for each backend: (1) starts and answers `initialize`; (2) accepts MCP servers; (3) runs a trivial prompt (create a file); (4) a refused permission is not bypassed (the file does not exist); (5) produces a valid `.choregos/result.json`; (6) respects `max_turns`; (7) the cost appears at the gateway. A backend that fails is disabled (`platform/backends`) until fixed.

### `choregos-tools` MCP sidecar (`packages/tools-mcp`)

Tools exposed to the agent (all writes go through the internal API):

| Tool | Effect |
| :-- | :-- |
| `report_finding(title, type, severity, evidence, suggested_fix, estimate)` | `POST /internal/runs/{id}/findings`; cap `policy.findings.max_per_run` |
| `request_scope_change(paths, justification)` | `POST …/scope-change`; answer `granted|pending|denied`; if `granted`, the runner extends `allowed_paths` live |
| `ask_human(question, options?)` | `POST …/question`; the agent must finish cleanly; the run ends `needs_human` |
| `get_ticket()` / `get_spec()` / `get_plan()` | read |
| `get_ci_logs(pr?, tail)` | CI logs of the last failure |
| `get_context()` | complete context pack |
| `search_memory(query, k)` | proxy to Ecphoria (if the agent does not mount the memory server directly) |
| `propose_fact(subject, content, provenance)` | Ecphoria `pending` queue |

## 2.3 Tekton `agent-stage` `Task` (`templates/*/tekton/agent-stage.yaml`)

```yaml
apiVersion: tekton.dev/v1
kind: Task
metadata: { name: choregos-agent-stage }
spec:
  params:
    - { name: run-id, type: string }
    - { name: api-url, type: string }
    - { name: runner-image, type: string, default: "ghcr.io/vargafoundation/choregos-runner:1.0.0" }
  workspaces: [{ name: source }]                       # ephemeral PVC (emptyDir volumeClaimTemplate)
  results: [{ name: result-url }, { name: status }]
  stepTemplate:
    securityContext: { runAsNonRoot: true, runAsUser: 1000, allowPrivilegeEscalation: false, capabilities: { drop: [ALL] } }
    env:
      - { name: CHOREGOS_RUN_ID, value: $(params.run-id) }
      - { name: CHOREGOS_API_URL, value: $(params.api-url) }
      - { name: CHOREGOS_RUN_TOKEN, valueFrom: { secretKeyRef: { name: run-$(params.run-id), key: token } } }
      - { name: OTEL_EXPORTER_OTLP_ENDPOINT, value: http://otel-collector.monitoring:4318 }
  steps:
    - name: run
      image: $(params.runner-image)
      workingDir: $(workspaces.source.path)
      script: |
        choregos-runner run --write-results $(results.result-url.path) $(results.status.path)
      resources: { requests: { cpu: "1", memory: 2Gi }, limits: { cpu: "2", memory: 6Gi } }
      timeout: 2h
  sidecars:
    - name: choregos-tools
      image: ghcr.io/vargafoundation/choregos-tools:1.0.0
      env: [{ name: CHOREGOS_RUN_TOKEN, valueFrom: { secretKeyRef: { name: run-$(params.run-id), key: token } } }]
```

The Tekton `Executor` (`packages/adapters/executor/tekton.py`): creates the `Secret run-<id>` (token, TTL) and the `PipelineRun` (Pipeline `choregos-agent` = git-clone → agent-stage, or the Task alone) in `proj-<slug>-runners`, with `runtimeClassName: gvisor` if the policy requires it, `activeDeadlineSeconds`, labels `choregos/run-id`, `choregos/project`; follows it via watch + CloudEvents; retrieves `result-url`; deletes the Secret; lets Tekton Results archive the logs. Chains signs the `PipelineRun`: the provenance of agent commits is verifiable.

K8s Job `Executor` (`k8s_job.py`): same image, same contract, `Job` + `ConfigMap` + `Secret`, `ttlSecondsAfterFinished`. ACA `Executor` (`azure_container_apps.py`): phase 6.

## 2.4 Sandbox

- Namespace `proj-<slug>-runners`: default-deny egress `NetworkPolicy` + allowances: internal API, LiteLLM, Ecphoria, OTel collector, SCM (via egress proxy), package registries (via proxy/mirror). Restricted DNS.
- Egress proxy (Envoy or Squid, namespace `choregos-egress`): per-project allowlist, logging, **credential injection** for private registries — secrets are never in the workspace.
- `RuntimeClass gvisor` (or Kata) according to `policy.sandbox.runtime`. Non-root, `seccompProfile: RuntimeDefault`, no `hostPath`, `ResourceQuota` per namespace.
- Shims in the image: `/usr/local/bin/kubectl`, `terraform`, `az`, `gcloud`, `aws` → binary that refuses and logs (defence in depth; no credential exists anyway).
- Git token: minted per run (`ScmAdapter.mint_token`, GitHub App, repository scope, 1 h TTL), injected via `.git-credentials` in memory (`credential.helper store` on tmpfs), never in the image nor in a global `env`.

## 2.5 Playbooks (`packages/playbooks`)

One folder per role: `prompt.md` (Jinja2: `{{ ticket }}`, `{{ spec }}`, `{{ allowed_paths }}`, `{{ context }}`, `{{ output_contract }}`), `README.md` (intent, invariants), `evals/` (cases + rubrics). Per-project overrides in `.choregos/playbooks/<role>/prompt.md` (merge by Jinja `{% block %}` blocks). The runner records `playbook_checksum` in the run.

Invariants common to all prompts: write `.choregos/result.json` conforming to the contract; never widen the scope without `request_scope_change`; report through `report_finding`; stop and `ask_human` rather than guess; conventional commits; do not touch Choregos configuration files; consult `search_memory` before an architecture decision.

| Role | Inputs | Expected outputs | Points of attention |
| :-- | :-- | :-- | :-- |
| `triage` | ticket, memory (similar tickets) | `size`, `risk`, `repos`, `questions[]`, `duplicate_of?` | 3 questions max; duplicate detection |
| `refine` | ticket, memory, code (read) | `spec_markdown` (problem, Given/When/Then criteria, out of scope, test plan, risk, rollback, flag?), `allowed_paths` | `allowed_paths` must be minimal and justified |
| `plan` | spec | `plan_markdown` (steps, files, migrations, commit order) | no code |
| `implement` | spec, plan, context | branch, commits, partial `evidence` | TDD encouraged; findings |
| `verify` | branch | complete `evidence`, reports | does not modify the code except tests |
| `review` | PR (diff), spec | structured review comments, verdict | blank context, backend ≠ implementer if policy |
| `fix_ci` | CI logs | commits | bounded |
| `address_review` | comments | commits + replies | bounded |
| `release_notes` | batch | markdown | |
| `verify_prod` | release, SLO | go/no-go + evidence | read-only |

Evals (in-house, in the style of `claude plugin eval`, `packages/playbooks/evals/runner.py`): fixture cases (toy repositories), rubrics graded by a judge model + deterministic assertions (file created, tests green, valid `result.json`). A minimum score per role blocks the playbooks' CI.
