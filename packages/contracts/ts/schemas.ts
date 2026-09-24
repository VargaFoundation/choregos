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

/** Sélection de mémoire injectée dans un stage, sous budget de tokens. Contenu **non fiable** : données, jamais instructions. */
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

/** Événement interne au format CloudEvents 1.0 (docs/plan/01 §1.7). */
export type Event = {
  specversion: "1.0";
  id: string;
  /** /choregos/orchestrator, /choregos/api, /choregos/runner/<run_id>… */
  source: string;
  type: "choregos.workitem.created" | "choregos.workitem.state_changed" | "choregos.workitem.closed" | "choregos.workitem.human_requested" | "choregos.workitem.human_decided" | "choregos.run.queued" | "choregos.run.started" | "choregos.run.progress" | "choregos.run.finished" | "choregos.finding.reported" | "choregos.finding.created" | "choregos.finding.duplicate" | "choregos.finding.dismissed" | "choregos.release.collected" | "choregos.release.departed" | "choregos.release.staged" | "choregos.release.approval_requested" | "choregos.release.promoted" | "choregos.release.verified" | "choregos.release.rolled_back" | "choregos.release.frozen" | "choregos.cost.recorded" | "choregos.cost.alert" | "choregos.project.provisioning.step" | "choregos.project.provisioning.completed" | "choregos.project.provisioning.failed";
  subject?: string | null;
  time: string;
  datacontenttype?: string;
  project_slug?: string | null;
  data?: {
    [key: string]: unknown;
  };
};

/** Problème découvert hors périmètre par un agent. */
export type Finding = {
  title: string;
  type: "perf" | "bug" | "security" | "tech-debt" | "docs" | "flaky-test" | "ux";
  severity: "low" | "medium" | "high" | "critical";
  /** chemin:ligne, sortie de test, requête… */
  evidence: string;
  suggested_fix?: string;
  estimate?: "S" | "M" | "L";
  out_of_scope_reason?: string;
};

/** Décision humaine acheminée vers un WorkflowInterpreter (front, board, commentaire, Slack, CLI). */
export type HumanDecision = {
  request_id?: string | null;
  kind: "approval" | "question" | "scope_change";
  approved?: boolean | null;
  answer?: string | null;
  granted_paths?: Array<string>;
  reason?: string | null;
  decided_by: string;
  decided_at: string;
  channel?: "web" | "tracker" | "slack" | "cli" | "api" | "board";
};

/** Événement externe normalisé par un adaptateur (docs/plan/01 §1.7). */
export type InboundEvent = {
  type: "tracker.item.created" | "tracker.item.updated" | "tracker.item.moved" | "tracker.item.commented" | "tracker.item.labeled" | "tracker.item.closed" | "scm.pr.opened" | "scm.pr.synchronized" | "scm.pr.review_submitted" | "scm.pr.merged" | "scm.pr.closed" | "scm.check.completed" | "ci.run.started" | "ci.run.succeeded" | "ci.run.failed" | "cd.app.synced" | "cd.app.degraded" | "cd.rollout.completed" | "cd.rollout.aborted" | "alert.fired" | "alert.resolved" | "human.decision";
  /** github, jira, gitlab, tekton, argocd, alertmanager, slack, web, cli */
  source: string;
  /** Identifiant unique de livraison (dédup/rejeu). */
  delivery_id: string;
  ts: string;
  project_slug?: string | null;
  work_item_key?: string | null;
  /** Identité externe à l'origine de l'événement. */
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
  /** Cron (5 champs), fuseau du projet. */
  schedule?: string;
  timezone?: string;
  /** Fenêtres autorisées, ex. 'Mon-Thu 09:00-18:00'. */
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

/** Politique d'un projet : budgets, approbations, tentatives, périmètre, sandbox, findings, release train. */
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
    /** Plafond de coût par ticket, par taille. */
    ticket_usd?: {
      S?: number;
      M?: number;
      L?: number;
      XL?: number;
    };
    /** Plafond par étape : rôle → taille → USD. La clé `default` s'applique aux rôles non listés. */
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
  /** Règles d'approbation humaine par type de décision. */
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
    /** Le backend de review doit différer de celui de l'implémentation. */
    cross_backend?: boolean;
    require_human_for_risk?: Array<"low" | "medium" | "high">;
  };
  /** Configuration par environnement (docs/plan/05 §5.2). */
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

/** Configuration d'un projet Choregos (colonne `projects.config`). */
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
    /** Surcharges projet : nom de profil → identifiant LiteLLM. */
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
  /** Référence de cluster (executor). */
  cluster?: string;
  notify?: {
    slack_channel?: string;
    emails?: Array<string>;
  };
  labels?: {
    [key: string]: string;
  };
};

/** Contrat orchestrateur → runner (docs/plan/01 §1.5). */
export type StageInput = {
  schema: "choregos/StageInput/v1";
  run_id: string;
  attempt: number;
  /** En-tête W3C traceparent propagé au runner. */
  trace_parent?: string;
  project: {
    slug: string;
    org: string;
    test_command?: string;
    lint_command?: string;
    typecheck_command?: string;
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
    /** Modèle réel derrière l'alias de plateforme : ce que vérifie un backend contraint. */
    provider_model?: string | null;
  };
  /** Clé virtuelle du run (budget = budget de l'étape). */
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
    /** Prompt rendu en clair (dev et fakes). */
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
    max_findings?: number;
  };
  callbacks: {
    api_url: string;
    run_token: string;
  };
};

/** Contrat runner → orchestrateur (docs/plan/01 §1.6). L'agent écrit .choregos/result.json ; le runner complète artifacts/evidence/diagnostics. */
export type StageResult = {
  schema: "choregos/StageResult/v1";
  status: "done" | "blocked" | "needs_human" | "failed";
  /** Cause quand status != done : limit, budget, scope, invalid_result, agent_error, ci… */
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
    /** Preuves nommées par le métier (evidence_facts). Valeurs simples : un nombre, un booléen, une date ou un mot se vérifient. */
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

/** Manifeste d'un template de stack (docs/plan/03 §3.2). */
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
    /** kind → type attendu, ex. tracker: github-issues */
    connectors: {
      [key: string]: string;
    };
    cluster_capabilities?: Array<string>;
  };
  defaults: {
    workflow?: string;
    policy?: string;
    models?: string;
    agent?: string;
  };
  inputs: Array<{
    name: string;
    type: "string" | "enum" | "list" | "bool" | "int" | "github-repo" | "cluster-ref" | "secret-ref";
    required?: boolean;
    default?: unknown;
    values?: Array<string>;
    description?: string;
  }>;
  steps: Array<string | {
    [key: string]: {
      [key: string]: unknown;
    };
  }>;
  scaffold?: string;
};

export type WorkflowSlug = string;

export type WorkflowIdentifier = string;

export type WorkflowActor = WorkflowAgentActor | WorkflowHumanActor | WorkflowSystemActor;

export type WorkflowAgentActor = {
  type: "agent";
  /** Rôle de l'agent. Les rôles du paquet — triage, refine, plan, implement, verify, review, fix_ci, address_review, release_notes, verify_prod, custom — gardent leur sens ; un métier nomme les siens (`sourcing`, `instruction_dossier`), et le playbook se résout par le nom du rôle. */
  role: string;
  /** profile:<name>, profile:by_size, ou un identifiant LiteLLM direct. */
  model?: string;
  /** Backend ACP imposé (sinon défaut du projet). */
  backend?: string;
  fresh_context?: boolean;
  max_turns?: number;
  max_minutes?: number;
  /** Nom du playbook (défaut : le rôle). */
  playbook?: string;
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

/** DSL de workflow Choregos : machine à états déclarative par projet (docs/plan/01 §1.4). */
export type Workflow = {
  apiVersion: "choregos/v1";
  kind: "Workflow";
  metadata: {
    name: WorkflowSlug;
    version: number;
    description?: string;
    extends?: string;
  };
  actors: {
    [key: string]: WorkflowActor;
  };
  states: {
    [key: string]: WorkflowState;
  };
  transitions: Array<WorkflowTransition>;
  defaults?: WorkflowDefaults;
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
  | Workflow;
