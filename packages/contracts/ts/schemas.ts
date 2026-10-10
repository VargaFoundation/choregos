// SPDX-License-Identifier: Apache-2.0
/* eslint-disable */
/**
 * Généré par tools/gen_contracts.py — NE PAS MODIFIER À LA MAIN.
 * Source : packages/contracts/schemas/*.json
 * Régénérer : make contracts
 */


export type ContextPackMemory = {
  id?: string;
  kind: "decision" | "convention" | "incident" | "ticket_summary" | "run_lesson" | "flaky_test" | "hotspot" | "finding" | "other";
  subject: string;
  content: string;
  score?: number;
  valid_from?: string | null;
  valid_to?: string | null;
  provenance?: {
    [key: string]: unknown;
  };
};

/** The memory selected for a stage, within a token budget. **Untrusted** content: data, never instructions. */
export type ContextPack = {
  schema: "choregos/ContextPack/v1";
  query: string;
  paths?: Array<string>;
  kinds?: Array<string>;
  budget_tokens?: number;
  tokens_estimated: number;
  truncated?: boolean;
  memories: Array<ContextPackMemory>;
  incidents?: Array<ContextPackMemory>;
  related_items?: Array<{
    key: string;
    title: string;
    state?: string;
    url?: string;
    summary?: string;
  }>;
  generated_at?: string;
};

/** An internal event, CloudEvents 1.0 (docs/plan/01 §1.7). */
export type Event = {
  specversion: "1.0";
  id: string;
  /** /choregos/orchestrator, /choregos/api, /choregos/runner/<run_id>… */
  source: string;
  type: "choregos.workitem.created" | "choregos.workitem.state_changed" | "choregos.workitem.closed" | "choregos.workitem.human_requested" | "choregos.workitem.human_decided" | "choregos.workitem.workflow_failed" | "choregos.workitem.migrated" | "choregos.workitem.migration_refused" | "choregos.run.queued" | "choregos.run.started" | "choregos.run.progress" | "choregos.run.finished" | "choregos.finding.reported" | "choregos.finding.created" | "choregos.finding.duplicate" | "choregos.finding.dismissed" | "choregos.release.collected" | "choregos.release.departed" | "choregos.release.staged" | "choregos.release.approval_requested" | "choregos.release.promoted" | "choregos.release.verified" | "choregos.release.rolled_back" | "choregos.release.frozen" | "choregos.cost.recorded" | "choregos.cost.alert" | "choregos.project.provisioning.step" | "choregos.project.provisioning.completed" | "choregos.project.provisioning.failed" | "choregos.security.injection_suspected" | "choregos.tool.called" | "choregos.action.proposed" | "choregos.action.decided" | "choregos.action.effect_done" | "choregos.action.effect_compensated" | "choregos.action.succeeded" | "choregos.action.failed";
  subject?: string | null;
  time: string;
  datacontenttype?: string;
  project_slug?: string | null;
  data?: {
    [key: string]: unknown;
  };
};

/** A problem an agent found outside its scope. */
export type Finding = {
  title: string;
  type: "perf" | "bug" | "security" | "tech-debt" | "docs" | "flaky-test" | "ux";
  severity: "low" | "medium" | "high" | "critical";
  /** path:line, test output, query… */
  evidence: string;
  suggested_fix?: string;
  estimate?: "S" | "M" | "L";
  out_of_scope_reason?: string;
};

/** A person's decision, routed to a WorkflowInterpreter (console, board, comment, Slack, CLI). */
export type HumanDecision = {
  request_id?: string | null;
  kind: "approval" | "question" | "scope_change" | "task";
  approved?: boolean | null;
  answer?: string | null;
  granted_paths?: Array<string>;
  reason?: string | null;
  /** A task: the values entered, written to the work item's fields. */
  values?: {
    [key: string]: unknown;
  };
  /** A task: the attested sentence, as it was shown. */
  attestation?: string | null;
  decided_by: string;
  decided_at: string;
  channel?: "web" | "tracker" | "slack" | "cli" | "api" | "board";
};

/** An external event, normalised by an adapter (docs/plan/01 §1.7). */
export type InboundEvent = {
  type: "tracker.item.created" | "tracker.item.updated" | "tracker.item.moved" | "tracker.item.commented" | "tracker.item.labeled" | "tracker.item.closed" | "scm.pr.opened" | "scm.pr.synchronized" | "scm.pr.review_submitted" | "scm.pr.merged" | "scm.pr.closed" | "scm.check.completed" | "ci.run.started" | "ci.run.succeeded" | "ci.run.failed" | "cd.app.synced" | "cd.app.degraded" | "cd.rollout.completed" | "cd.rollout.aborted" | "alert.fired" | "alert.resolved" | "human.decision";
  /** github, jira, gitlab, tekton, argocd, alertmanager, slack, web, cli */
  source: string;
  /** Unique delivery identifier (deduplication, replay). */
  delivery_id: string;
  ts: string;
  project_slug?: string | null;
  work_item_key?: string | null;
  /** The external identity behind the event. */
  actor?: string | null;
  payload?: {
    [key: string]: unknown;
  };
};

export type PolicyApprovalRule = {
  required: "always" | "never" | "by_size" | "by_risk";
  group?: string;
  sizes?: Array<"S" | "M" | "L" | "XL">;
  risks?: Array<"low" | "medium" | "high">;
  timeout_hours?: number;
};

export type PolicyTrainEnv = {
  mode?: "auto_sync" | "train";
  /** Cron (5 fields), in the project's time zone. */
  schedule?: string;
  timezone?: string;
  /** Allowed windows, e.g. 'Mon-Thu 09:00-18:00'. */
  windows?: Array<string>;
  batch_min?: number;
  batch_max?: number;
  soak_minutes?: number;
  approval?: {
    group?: string;
    required?: boolean;
    timeout_hours?: number;
  };
  canary?: {
    steps?: Array<number>;
    analysis?: string;
    step_minutes?: Array<number>;
  };
  express_lane?: {
    label?: string;
    soak_minutes?: number;
    skip_schedule?: boolean;
    approval?: {
      group?: string;
      required?: boolean;
    };
  };
  freeze?: boolean;
  auto_rollback?: boolean;
  freeze_on_rollback?: boolean;
  terraform?: {
    via?: "atlantis" | "none";
    apply_requires_approval?: boolean;
  };
};

/** A project's policy: budgets, approvals, attempts, scope, sandbox, findings, release train. */
export type Policy = {
  apiVersion: "choregos/v1";
  kind: "Policy";
  metadata: {
    name: string;
    version: number;
    preset?: "solo" | "team" | "regulated";
    description?: string;
  };
  budgets?: {
    /** Cost ceiling per work item, by size. */
    ticket_usd?: {
      S?: number;
      M?: number;
      L?: number;
      XL?: number;
    };
    /** Ceiling per step: role → size → USD. The `default` key applies to the roles not listed. */
    stage_usd?: {
      [key: string]: {
        S?: number;
        M?: number;
        L?: number;
        XL?: number;
      };
    };
    max_turns?: {
      [key: string]: number;
    };
    max_minutes?: {
      [key: string]: number;
    };
    daily_project_usd?: number;
    alert_at_ratio?: number;
  };
  /** Human approval rules, by kind of decision. */
  approvals?: {
    spec?: PolicyApprovalRule;
    merge?: PolicyApprovalRule;
    prod?: PolicyApprovalRule;
    scope_change?: PolicyApprovalRule;
  };
  attempts?: {
    default_max?: number;
    by_role?: {
      [key: string]: number;
    };
    dod_iterations?: number;
  };
  scope?: {
    auto_grant_max_files?: number;
    deny_paths?: Array<string>;
    max_diff_files?: number;
    max_diff_lines?: number;
  };
  sandbox?: {
    runtime?: "default" | "gvisor" | "kata";
    deny_commands?: Array<string>;
    network?: {
      allow_domains?: Array<string>;
      deny_by_default?: boolean;
    };
    llm_security_analyzer?: boolean;
    /** A permission request of an unknown kind: let through and logged (allow), or refused (reject) */
    unknown_requests?: "allow" | "reject";
    /** A prompt injection spotted in what the agent will read: log it (warn), stop the step (block), do not look (ignore) */
    prompt_injection?: "ignore" | "warn" | "block";
  };
  findings?: {
    max_per_run?: number;
    dedupe_threshold?: number;
    auto_agent_ready_sizes?: Array<"S" | "M" | "L" | "XL">;
    notify_severities?: Array<"low" | "medium" | "high" | "critical">;
    false_positive_alert_ratio?: number;
  };
  memory?: {
    enabled?: boolean;
    context_budget_tokens?: {
      [key: string]: number;
    };
    auto_accept_after_runs?: number;
    read_timeout_ms?: number;
  };
  review?: {
    /** The review backend must differ from the implementation's. */
    cross_backend?: boolean;
    require_human_for_risk?: Array<"low" | "medium" | "high">;
  };
  /** Configuration per environment (docs/plan/05 §5.2). */
  release_train?: {
    [key: string]: PolicyTrainEnv;
  };
};

export type ProjectProfile = string | {
  litellm_model: string;
  params?: {
    [key: string]: unknown;
  };
  max_turns_factor?: number;
};

/** A Choregos project's configuration (the `projects.config` column). */
export type Project = {
  slug: string;
  org: string;
  display_name?: string;
  template_ref?: string;
  repo?: {
    url: string;
    default_branch: string;
    language?: "python" | "node" | "go" | "java" | "dotnet" | "rust" | "other";
    clone_depth?: number;
    branch_prefix?: string;
    test_command?: string;
    lint_command?: string;
    typecheck_command?: string;
  };
  envs?: Array<string>;
  agent?: {
    default_backend?: string;
    allowed_backends?: Array<string>;
  };
  models?: {
    /** Project overrides: profile name → LiteLLM identifier. */
    profiles?: {
      [key: string]: ProjectProfile;
    };
    allow_unvalidated?: boolean;
  };
  gitops?: {
    repo_url?: string;
    path_prefix?: string;
    apps?: Array<string>;
  };
  /** Cluster reference (executor). */
  cluster?: string;
  notify?: {
    slack_channel?: string;
    emails?: Array<string>;
  };
  /** Catalogue tools this project may call, by name. Empty = none. ['*'] opens the whole catalogue. */
  tools?: Array<string>;
  /** The organisation's groups this project belongs to. They decide what the deployment opens to it in the tool catalogue. */
  groups?: Array<string>;
  labels?: {
    [key: string]: string;
  };
};

/** Orchestrator → runner contract (docs/plan/01 §1.5). */
export type StageInput = {
  schema: "choregos/StageInput/v1";
  run_id: string;
  attempt: number;
  /** W3C traceparent header, passed on to the runner. */
  trace_parent?: string;
  project: {
    slug: string;
    org: string;
    test_command?: string;
    lint_command?: string;
    typecheck_command?: string;
    /** Commands that MEASURE a boolean fact: fact name → command, exit code 0 = true. The runner runs them after the step, and the measured fact overrides the one the agent declared, as the tests override tests_passed. At the root of the project's configuration (dod.facts), not under repo: a project without a repository must be able to prove something. */
    fact_commands?: {
      [key: string]: string;
    };
  };
  work_item: {
    key: string;
    title: string;
    body?: string;
    url?: string;
    size?: "S" | "M" | "L" | "XL";
    risk?: "low" | "medium" | "high";
    links?: {
      spec_comment?: string | null;
      plan_comment?: string | null;
      pr?: string | null;
    };
  };
  transition: {
    id: string;
    role: string;
    from: string;
    to: string;
    outputs?: Array<string>;
    inputs?: Array<string>;
  };
  repo?: {
    url: string;
    base_branch: string;
    work_branch: string;
    clone_depth?: number;
  };
  agent: {
    backend: string;
    launch?: {
      command?: Array<string>;
      env?: {
        [key: string]: string;
      };
      files?: {
        [key: string]: string;
      };
    };
  };
  model: {
    litellm_model: string;
    base_url: string;
    api_format: "openai" | "anthropic" | "gemini";
    params?: {
      [key: string]: unknown;
    };
    /** The real model behind the platform alias: what a constrained backend checks. */
    provider_model?: string | null;
  };
  /** The run's virtual key (budget = the step's budget). */
  gateway_key?: string;
  budget: {
    usd: number;
    max_turns: number;
    max_minutes: number;
  };
  allowed_paths: Array<string>;
  context_pack_url?: string | null;
  playbook: {
    ref: string;
    prompt_url?: string | null;
    /** The rendered prompt in plain text (dev and fakes). */
    prompt?: string | null;
  };
  tools?: {
    mcp?: {
      [key: string]: {
        url?: string;
        command?: Array<string>;
        env?: {
          [key: string]: string;
        };
      };
    };
  };
  permissions?: {
    write_paths?: Array<string>;
    deny_commands?: Array<string>;
    allow_domains?: Array<string>;
    dod_iterations?: number;
    unknown_requests?: "allow" | "reject";
    max_findings?: number;
  };
  callbacks: {
    api_url: string;
    run_token: string;
  };
  /** The registry agent's skills (ADR 0033): name, version, digest. The runner reads the files through the internal API and checks the digest before laying them down. */
  skills?: Array<{
    slug: string;
    version: number;
    digest: string;
  }>;
};

/** Runner → orchestrator contract (docs/plan/01 §1.6). The agent writes .choregos/result.json; the runner completes artifacts/evidence/diagnostics. */
export type StageResult = {
  schema: "choregos/StageResult/v1";
  status: "done" | "blocked" | "needs_human" | "failed";
  /** Cause when status != done: limit, budget, scope, invalid_result, agent_error, ci… */
  reason?: string | null;
  summary: string;
  outputs?: {
    size?: "S" | "M" | "L" | "XL";
    risk?: "low" | "medium" | "high";
    allowed_paths?: Array<string>;
    spec_markdown?: string;
    plan_markdown?: string;
    review_markdown?: string;
    verdict?: "approve" | "request_changes" | "comment";
    release_notes_markdown?: string;
    duplicate_of?: string | null;
    questions_asked?: number;
    [key: string]: unknown;
  };
  artifacts?: {
    branch?: string | null;
    commits?: Array<string>;
    pr_url?: string | null;
    transcript_url?: string | null;
    context_pack_url?: string | null;
    reports?: {
      [key: string]: string;
    };
  };
  evidence?: {
    /** Evidence named by the business (evidence_facts). Simple values: a number, a boolean, a date or a word can be checked. */
    facts?: {
      [key: string]: string | number | number | boolean;
    } | null;
    tests_passed?: boolean | null;
    tests_run?: number | null;
    tests_failed?: number | null;
    coverage_delta?: number | null;
    lint?: "ok" | "failed" | "skipped" | null;
    typecheck?: "ok" | "failed" | "skipped" | null;
    security_scan?: "ok" | "failed" | "skipped" | null;
    diff_files?: number | null;
    diff_lines?: number | null;
    /** What the platform measured itself (ADR 0045): the names of the fields above it ran or computed — tests, lint, typecheck, diff — and `facts.<name>` for a business fact a command measured. Written by the runner, never by the agent: an agent that sets it is overwritten. A field not listed comes from the agent's own account; null means a runner too old to say. */
    measured?: Array<string> | null;
  };
  questions?: Array<{
    text: string;
    options?: Array<string>;
  }>;
  findings?: Array<Finding>;
  scope_changes_requested?: Array<{
    paths: Array<string>;
    justification: string;
  }>;
  diagnostics?: {
    turns?: number;
    tool_calls?: number;
    permission_denials?: number;
    duration_s?: number;
    agent_exit?: "normal" | "cancelled" | "crashed" | "timeout" | "limit";
    dod_iterations?: number;
    result_repairs?: number;
  };
};

/** A stack template's manifest (docs/plan/03 §3.2). */
export type Template = {
  apiVersion: "choregos/v1";
  kind: "Template";
  metadata: {
    name: string;
    version: string;
    display: string;
    description?: string;
  };
  requires: {
    /** kind → expected type, e.g. tracker: github-issues */
    connectors: {
      [key: string]: string;
    };
    cluster_capabilities?: Array<string>;
    /** Azure resource providers to register, e.g. Microsoft.App/managedEnvironments */
    azure_capabilities?: Array<string>;
    /** the ORGANISATION's connectors that the workflows call, by name → capability (S20-07), e.g. annuaire: identity */
    org_connectors?: {
      [key: string]: string;
    };
  };
  defaults: {
    /** a single workflow: the form used before `workflows` */
    workflow?: string;
    /** the workflows shipped (ADR 0031): `template:<name>@<v>`, a core template, or a path relative to the template's folder (`workflows/arrivee.yaml`) */
    workflows?: Array<string>;
    /** the default workflow's name; otherwise the first one shipped */
    default_workflow?: string;
    /** the project's routing rules, as PUT /projects/{id}/workflow-routing */
    routing?: Array<{
      when: {
        labels_any?: Array<string>;
        labels_all?: Array<string>;
        item_type?: string;
      };
      workflow: string;
    }>;
    policy?: string;
    models?: string;
    agent?: string;
    /** the agents installed in the organisation when a project is born, if they are not there: files of the template in the `POST /orgs/{org}/agents` format, or `catalogue:<slug>` (S20-07, ADR 0040) */
    agents?: Array<string>;
    /** the skills installed the same way: folders of the template, each with its SKILL.md, or `catalogue:<slug>` (S20-07) */
    skills?: Array<string>;
    /** what a PLUGIN installs in the project when it is born: an installer name → a folder of the template (`ontology: ./ontology`). Without a plugin to install it, the project is born without it, and the audit log says so (S20-09) */
    extensions?: {
      [key: string]: string;
    };
  };
  inputs: Array<{
    name: string;
    type: "string" | "enum" | "list" | "bool" | "int" | "github-repo" | "cluster-ref" | "secret-ref";
    required?: boolean;
    default?: unknown;
    values?: Array<string>;
    description?: string;
  }>;
  /** the provisioning steps; none for a project without a repository (S20-07) */
  steps: Array<string | {
    [key: string]: {
      [key: string]: unknown;
    };
  }>;
  scaffold?: string;
};

export type UiManifestPath = string;

export type UiManifestForm = {
  kind: "form";
  title: string;
  description?: string;
  /** the JSON Schema of the values; the console derives the form from it */
  schema: {
    [key: string]: unknown;
  };
  read: UiManifestPath;
  write: UiManifestPath;
  write_method?: "PUT" | "PATCH" | "POST";
};

export type UiManifestColumn = {
  key: string;
  label: string;
  format?: "text" | "date" | "badge" | "code";
};

export type UiManifestTable = {
  kind: "table";
  title: string;
  description?: string;
  list: UiManifestPath;
  row_key?: string;
  columns: Array<UiManifestColumn>;
  row_actions?: Array<UiManifestAction>;
};

export type UiManifestAction = {
  kind: "action";
  label: string;
  description?: string;
  path: UiManifestPath;
  method?: "POST" | "PUT" | "PATCH" | "DELETE";
  /** the sentence to confirm before acting */
  confirm?: string;
  danger?: boolean;
  /** the gesture requires a fresh sign-in */
  reauth?: boolean;
  /** the JSON Schema of the parameters asked for before acting */
  params?: {
    [key: string]: unknown;
  };
};

export type UiManifestSecretOnce = {
  kind: "secret_once";
  label: string;
  description?: string;
  path: UiManifestPath;
  method?: "POST" | "PUT";
  confirm?: string;
  params?: {
    [key: string]: unknown;
  };
  /** the response field that carries the secret, shown once and never kept */
  secret_field: string;
};

/** An administration section declared by a plugin, as DATA: the console renders it with its own blocks, no plugin code runs there (ADR 0032). Paths are relative to /api/v1; `{org}` is the current organisation, `{id}` the row's identifier. */
export type UiManifest = {
  id: string;
  title: string;
  description?: string;
  scope: "platform" | "organisation";
  /** a core permission, e.g. member:manage; platform:admin for the platform scope */
  permission: string;
  blocks: Array<UiManifestForm | UiManifestTable | UiManifestAction | UiManifestSecretOnce>;
};

export type WorkflowSlug = string;

export type WorkflowIdentifier = string;

export type WorkflowActor = WorkflowAgentActor | WorkflowHumanActor | WorkflowSystemActor;

export type WorkflowAgentActor = {
  type: "agent";
  /** The agent's role. The package's roles — triage, refine, plan, implement, verify, review, fix_ci, address_review, release_notes, verify_prod, custom — keep their meaning; a business names its own (`sourcing`, `instruction_dossier`), and the playbook is resolved by the role's name. */
  role: string;
  /** profile:<name>, profile:by_size, or a direct LiteLLM identifier. */
  model?: string;
  /** The ACP backend to use (otherwise the project's default). */
  backend?: string;
  fresh_context?: boolean;
  max_turns?: number;
  max_minutes?: number;
  /** The playbook's name (default: the role). */
  playbook?: string;
  /** A registry agent (ADR 0033): `slug`, or `slug@version`. Its version — the project's pinned one, otherwise the latest — sets the instructions, model, limits and budget; its instructions take precedence over the playbook. */
  agent?: string;
};

export type WorkflowHumanActor = {
  type: "human";
  group: string;
  sla_hours?: number;
  escalate_to?: string;
};

export type WorkflowSystemActor = {
  type: "system";
};

export type WorkflowState = {
  display: string;
  tracker?: {
    status?: string;
    label?: string;
  };
  terminal?: boolean;
  kind?: "work" | "wait" | "terminal";
  /** A production state: it can only be reached `via: release_train` (before #175: the name's `deployed_prod` prefix). */
  production?: boolean;
};

export type WorkflowTransition = unknown | unknown;

export type WorkflowRetry = {
  to: WorkflowIdentifier;
  max_attempts: number;
  escalate_to: WorkflowIdentifier;
};

export type WorkflowGateRef = string | {
  name: string;
  params?: {
    [key: string]: unknown;
  };
};

export type WorkflowDefaults = {
  from_any_agent_state?: {
    on_question?: WorkflowIdentifier;
    on_budget_exceeded?: WorkflowIdentifier;
    on_timeout?: WorkflowIdentifier;
  };
  needs_human?: {
    on_answer?: "resume";
    on_abandon?: WorkflowIdentifier;
  };
};

export type WorkflowActionEffect = {
  effect: string;
  with?: {
    [key: string]: unknown;
  };
  compensate?: {
    [key: string]: unknown;
  };
};

export type WorkflowActionApproval = {
  approvers?: Array<{
    role?: "developer" | "release_captain" | "project_owner" | "org_admin";
    min?: number;
  }>;
  step_up_minutes?: number;
  separation_of_duties?: boolean;
};

/** title, reason and parameters rendered with the work item's fields; a human approval when an operation requires one or when `approval` is declared */
export type WorkflowTransitionAction = {
  kind: string;
  title: string;
  justification?: string;
  params?: {
    [key: string]: unknown;
  };
  effects: Array<WorkflowActionEffect>;
  approval?: WorkflowActionApproval;
  /** not before this date, taken from a work item's field: `fields.date_arrivee - 10d` */
  not_before?: string;
};

export type WorkflowTask = {
  title?: string;
  instructions?: string;
  /** an object JSON Schema; each property is a field of the work item */
  form: {
    [key: string]: unknown;
  };
  /** what the person attests, word for word */
  attest?: string;
};

/** Choregos workflow DSL: a declarative state machine per project (docs/plan/01 §1.4). */
export type Workflow = {
  apiVersion: "choregos/v1";
  kind: "Workflow";
  metadata: {
    name: WorkflowSlug;
    version: number;
    description?: string;
    extends?: string;
    /** JSON Schema of the fields of a work item of this workflow (ADR 0031). */
    inputs?: {
      [key: string]: unknown;
    };
  };
  actors: {
    [key: string]: WorkflowActor;
  };
  states: {
    [key: string]: WorkflowState;
  };
  transitions: Array<WorkflowTransition>;
  defaults?: WorkflowDefaults;
  /** Where the workflow starts. Optional: the parser writes the first declared state there (§1.4). The field exists because the order of an object's keys does not survive jsonb storage, and a workflow that believes it starts at its terminal state closes the work item without doing anything. */
  initial?: string;
};

export type ChoregosContract =
  | ContextPack
  | Event
  | Finding
  | HumanDecision
  | InboundEvent
  | Policy
  | Project
  | StageInput
  | StageResult
  | Template
  | UiManifest
  | Workflow;
