# 2. Orchestrateur, agents, runner

## 2.1 Orchestrateur Temporal (`apps/orchestrator`)

### Workflows

| Workflow | ID | Rôle |
| :-- | :-- | :-- |
| `WorkflowInterpreter` | `wi-<project>-<tracker_key>` | Exécute le DSL épinglé pour un ticket |
| `ReleaseTrain` | `train-<project>-<env>` | Sérialise les déploiements d'un environnement (section 5) |
| `FindingsTriage` | `findings-<project>` | Déduplique, classe, crée les tickets (section 5) |
| `ProjectProvisioning` | `prov-<project>` | Exécute les étapes du template (section 3) |
| `MemoryIngestion` | `mem-<project>` | Import planifié tracker/SCM/CD → Ecphoria (section 4) |
| `EvalMatrix` | `evals-<project>` | Évals nocturnes backend × modèle (section 4) |

Task queues : `orchestrator` (workflows + activités légères), `executor` (création/suivi de runs), `tracker` (appels API externes, rate-limités), `memory`. Workers : `apps/orchestrator/worker.py --queues …` ; déploiement 2+ réplicas par queue (section 6).

### `WorkflowInterpreter` — algorithme

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

    # signaux
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
                continue  # état d'attente humaine (board)
            if t.via == "release_train":
                await self.act(signal_train, project, t.train.env, wi, self.last_pr)
                deploy = await self.wait_for(DeployResult, timeout=t.timeout)
                self.state = t.to if deploy.ok else wf.on_rollback_state
            elif t.actor.type == "agent":
                res = await self.run_stage(wi, t, project, policy)
                self.state = self.after_stage(t, res, wf)
            elif t.actor.type == "human":
                dec = await self.wait_human(wi, t, project, policy)  # crée human_request, SLA, rappels
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
        model = await self.act(resolve_model, project, t.actor.model, wi.size)  # profils → litellm_model
        key = await self.act(mint_gateway_key, project, wi, t, attempt, budget, model)
        ctx = await self.act(build_context_pack, project, wi, t)  # Ecphoria + tickets liés + logs CI
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
            res.summary += " (budget ticket dépassé)"
        return res

    def after_stage(self, t, res, wf) -> str:
        if res.status == "done":
            gates_ok = await_gates_sync(
                t.gates
            )  # évaluées par activité ; gates asynchrones (CI) → attente signal
            return t.to if gates_ok else self.retry_or_escalate(t, wf)
        if res.status == "needs_human":
            return wf.defaults.on_question
        return self.retry_or_escalate(t, wf)  # failed | blocked

    def retry_or_escalate(self, t, wf) -> str:
        r = t.on_fail
        return r.to if self.attempts[t.id] < r.max_attempts else r.escalate_to
```

Activités (`apps/orchestrator/activities/`) : `mirror_state`, `resolve_model`, `mint_gateway_key`, `collect_spend`, `revoke_key`, `build_context_pack`, `start_run`, `await_run` (avec heartbeat et annulation), `record_run`, `update_ticket_status_comment`, `emit_finding`, `create_human_request`, `notify`, `evaluate_gate`, `signal_train`, `close_out`, `write_run_lesson` (mémoire). Toutes idempotentes par `(run_id|work_item_id, step)`. Retries Temporal : réseau 5× exponentiel ; erreurs métier (`ApplicationError(non_retryable=True)`).

Attente humaine : `create_human_request` poste un commentaire structuré sur le ticket (et une notification), puis `wait_for(HumanDecision)` avec timers : rappel à `sla_hours`, escalade à `escalate_to` ensuite. Une décision peut arriver par le board (carte déplacée), par un commentaire `/choregos approve|reject|answer …` (parsé par le webhook), par le front, ou par le CLI.

Gates asynchrones : `ci_green` et `review_approved` attendent des `InboundEvent` (`scm.check.completed`, `scm.pr.review_submitted`) ; timeout → `retry_or_escalate`. `scope_respected`, `evidence_present`, `diff_size_max`, `no_secrets` sont synchrones (activité `evaluate_gate` qui appelle `ScmAdapter.compare`).

Migration : signal `control(migrate, workflow_def_id)` → l'interpréteur vérifie que l'état courant existe dans la nouvelle définition (sinon mapping fourni) puis `continue_as_new` avec la nouvelle définition.

## 2.2 Le runner (`packages/runner`) — client ACP headless

Image `ghcr.io/vargafoundation/choregos-runner:<version>` : Debian slim + Python 3.12 + `git`, `gh`, `glab`, `jq`, `ripgrep`, outils de test usuels (node 22, pnpm, uv, go, java 21 en variantes d'image), scanners (Semgrep, Trivy, gitleaks), **agents ACP préinstallés** (OpenHands `agent-server`, `claude-agent-acp`, `codex-acp`, Gemini CLI, Goose, OpenCode) à versions épinglées dans `packages/runner/backends/versions.lock`.

### Algorithme

```
1. input  ← GET /internal/runs/{id}/input (run_token)            ; si résultat déjà posté → exit 0 (idempotence)
2. clone  ← git clone --depth N repo ; checkout work_branch (créée depuis base si absente) ; git config user = choregos-bot
3. ctx    ← télécharger context_pack ; écrire .choregos/context.md ; écrire .choregos/task.md (ticket + spec + consignes de sortie)
4. tools  ← démarrer sidecar MCP choregos-tools (localhost:7777) avec run_token ; écrire la config MCP du backend
5. agent  ← backend.launch_spec(ctx) : écrire fichiers de config (modèle, MCP), exporter model_env ; spawn processus ; ACP initialize
6. sess   ← session/new(cwd=/workspace, mcpServers=[choregos, memory, github-readonly])
7. prompt ← session/prompt(playbook rendu + task.md)              ; boucle d'événements :
      - session/update → journaliser (batch POST /internal/runs/{id}/events), compter tours/outils
      - session/request_permission → PolicyEngine.decide(op) :
            write hors allowed_paths → deny + message "report_finding / request_scope_change"
            commande dans deny_commands ou motif dangereux → deny
            réseau hors allowlist → deny (le sandbox bloque aussi)
            sinon allow (journalisé)
      - dépassement max_turns / max_minutes → cancel session → status failed(reason=limit)
8. dod    ← boucle externe (max policy.dod_iterations, défaut 3) :
      a. lancer `make test` / commande du dépôt ; lint ; typecheck
      b. si échec → session/prompt("Les vérifications échouent : <sortie tronquée>. Corrige.") ; retour 7
9. scope  ← git diff --name-only base..HEAD ; fichiers hors allowed_paths → revert de ces fichiers + prompt de rappel (1 fois) ; s'il en reste → status blocked(reason=scope)
10. result← lire .choregos/result.json ; valider schéma ; invalide → prompt de réparation (2 fois) ; compléter evidence/artifacts/diagnostics
11. publish← commit conventionnel (si diff), push ; upload transcript (JSONL des events ACP) + rapports vers object store
12. POST /internal/runs/{id}/result ; exit 0
```

Sorties standardisées : code 0 (résultat posté, quel que soit `status`), 10 (input introuvable), 20 (clone impossible), 30 (agent injoignable — backend cassé), 40 (résultat invalide après réparations). Tout autre code = crash → l'activité `await_run` rejoue une fois avec le même `run_id`.

### Backends (`packages/runner/backends/`)

| Backend | `launch_spec` | `model_env` | Notes |
| :-- | :-- | :-- | :-- |
| `openhands` (défaut) | `openhands acp` (ou `agent-server` + client ACP) ; skills `.openhands/skills/` ; `AGENTS.md` lu nativement | `LLM_MODEL=<litellm_model>`, `LLM_BASE_URL`, `LLM_API_KEY` | Sécurité : `LLMSecurityAnalyzer` activable par politique |
| `claude-code` | `claude-agent-acp` (adaptateur Zed) ; `CLAUDE.md` → lien vers `AGENTS.md` ; `settings.json` projet avec hooks de secours | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` | Modèle Claude uniquement (validation projet) |
| `codex` | `codex-acp` ; `AGENTS.md` natif | `OPENAI_BASE_URL`, `OPENAI_API_KEY`, modèle via config | |
| `gemini-cli` | `gemini --experimental-acp` | `GOOGLE_GEMINI_BASE_URL` (via LiteLLM), clé | |
| `goose` | `goose acp` | provider `openai` pointé sur LiteLLM | |
| `opencode` | `opencode acp` ou `opencode serve` + SDK | `opencode.json` provider `openai-compatible` → LiteLLM | API mouvante : suite de conformité obligatoire |
| `copilot-cli` | `copilot --acp` | selon doc GitHub | Optionnel |

Suite de conformité (`tests/conformance/backends/`) exécutée en CI pour chaque backend : (1) démarre et répond à `initialize` ; (2) accepte les serveurs MCP ; (3) exécute un prompt trivial (créer un fichier) ; (4) une permission refusée n'est pas contournée (le fichier n'existe pas) ; (5) produit `.choregos/result.json` valide ; (6) respecte `max_turns` ; (7) le coût apparaît au gateway. Un backend qui échoue est désactivé (`platform/backends`) jusqu'à correction.

### Sidecar MCP `choregos-tools` (`packages/tools-mcp`)

Outils exposés à l'agent (toutes les écritures passent par l'API interne) :

| Outil | Effet |
| :-- | :-- |
| `report_finding(title, type, severity, evidence, suggested_fix, estimate)` | `POST /internal/runs/{id}/findings` ; plafond `policy.findings.max_per_run` |
| `request_scope_change(paths, justification)` | `POST …/scope-change` ; réponse `granted|pending|denied` ; si `granted`, le runner étend `allowed_paths` à chaud |
| `ask_human(question, options?)` | `POST …/question` ; l'agent doit terminer proprement ; le run se termine `needs_human` |
| `get_ticket()` / `get_spec()` / `get_plan()` | lecture |
| `get_ci_logs(pr?, tail)` | logs CI du dernier échec |
| `get_context()` | context pack complet |
| `search_memory(query, k)` | proxy vers Ecphoria (si l'agent ne monte pas le serveur mémoire directement) |
| `propose_fact(subject, content, provenance)` | file `pending` d'Ecphoria |

## 2.3 `Task` Tekton `agent-stage` (`templates/*/tekton/agent-stage.yaml`)

```yaml
apiVersion: tekton.dev/v1
kind: Task
metadata: { name: choregos-agent-stage }
spec:
  params:
    - { name: run-id, type: string }
    - { name: api-url, type: string }
    - { name: runner-image, type: string, default: "ghcr.io/vargafoundation/choregos-runner:1.0.0" }
  workspaces: [{ name: source }]                       # PVC éphémère (emptyDir volumeClaimTemplate)
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

L'`Executor` Tekton (`packages/adapters/executor/tekton.py`) : crée le `Secret run-<id>` (token, TTL) et le `PipelineRun` (Pipeline `choregos-agent` = git-clone → agent-stage, ou la Task seule) dans `proj-<slug>-runners`, avec `runtimeClassName: gvisor` si la politique l'exige, `activeDeadlineSeconds`, labels `choregos/run-id`, `choregos/project`; suit via watch + CloudEvents ; récupère `result-url` ; supprime le Secret ; laisse Tekton Results archiver les logs. Chains signe le `PipelineRun` : la provenance des commits d'agent est vérifiable.

`Executor` Job K8s (`k8s_job.py`) : même image, même contrat, `Job` + `ConfigMap` + `Secret`, `ttlSecondsAfterFinished`. `Executor` ACA (`azure_container_apps.py`) : phase 6.

## 2.4 Sandbox

- Namespace `proj-<slug>-runners` : `NetworkPolicy` default-deny egress + autorisations : API interne, LiteLLM, Ecphoria, collecteur OTel, SCM (via proxy d'egress), registres de paquets (via proxy/miroir). DNS restreint.
- Proxy d'egress (Envoy ou Squid, namespace `choregos-egress`) : allowlist par projet, journalisation, **injection de credentials** pour les registres privés — les secrets ne sont jamais dans le workspace.
- `RuntimeClass gvisor` (ou Kata) selon `policy.sandbox.runtime`. Non-root, `seccompProfile: RuntimeDefault`, pas de `hostPath`, `ResourceQuota` par namespace.
- Shims dans l'image : `/usr/local/bin/kubectl`, `terraform`, `az`, `gcloud`, `aws` → binaire qui refuse et journalise (défense en profondeur ; aucun credential n'existe de toute façon).
- Token Git : minté par run (`ScmAdapter.mint_token`, App GitHub, portée dépôt, TTL 1 h), injecté via `.git-credentials` en mémoire (`credential.helper store` sur tmpfs), jamais dans l'image ni en `env` globale.

## 2.5 Playbooks (`packages/playbooks`)

Un dossier par rôle : `prompt.md` (Jinja2 : `{{ ticket }}`, `{{ spec }}`, `{{ allowed_paths }}`, `{{ context }}`, `{{ output_contract }}`), `README.md` (intention, invariants), `evals/` (cas + rubriques). Surcharges par projet dans `.choregos/playbooks/<role>/prompt.md` (fusion par blocs Jinja `{% block %}`). Le runner enregistre `playbook_checksum` dans le run.

Invariants communs à tous les prompts : écrire `.choregos/result.json` conforme au contrat ; ne jamais élargir le périmètre sans `request_scope_change` ; signaler via `report_finding` ; s'arrêter et `ask_human` plutôt que deviner ; commits conventionnels ; ne pas toucher aux fichiers de configuration Choregos ; consulter `search_memory` avant une décision d'architecture.

| Rôle | Entrées | Sorties attendues | Points d'attention |
| :-- | :-- | :-- | :-- |
| `triage` | ticket, mémoire (tickets similaires) | `size`, `risk`, `repos`, `questions[]`, `duplicate_of?` | 3 questions max ; détection de doublon |
| `refine` | ticket, mémoire, code (lecture) | `spec_markdown` (problème, critères Given/When/Then, hors-périmètre, plan de test, risque, rollback, flag ?), `allowed_paths` | `allowed_paths` doit être minimal et justifié |
| `plan` | spec | `plan_markdown` (étapes, fichiers, migrations, ordre des commits) | pas de code |
| `implement` | spec, plan, context | branche, commits, `evidence` partielle | TDD encouragé ; findings |
| `verify` | branche | `evidence` complète, rapports | ne modifie pas le code sauf tests |
| `review` | PR (diff), spec | commentaires de review structurés, verdict | contexte vierge, backend ≠ implémenteur si politique |
| `fix_ci` | logs CI | commits | borné |
| `address_review` | commentaires | commits + réponses | borné |
| `release_notes` | batch | markdown | |
| `verify_prod` | release, SLO | go/no-go + preuves | lecture seule |

Évals (`claude plugin eval`-like maison, `packages/playbooks/evals/runner.py`) : cas fixtures (dépôts jouets), rubriques notées par un modèle juge + assertions déterministes (fichier créé, tests verts, `result.json` valide). Score minimal par rôle bloque la CI des playbooks.
