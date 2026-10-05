// SPDX-License-Identifier: Apache-2.0
/* eslint-disable */
/**
 * Généré par tools/gen_contracts.py — NE PAS MODIFIER À LA MAIN.
 * Source : packages/contracts/openapi.yaml
 * Régénérer : make contracts
 */


import type * as S from "./schemas";

/** RFC 9457 */
export type Problem = {
  type: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
  errors?: Array<{
    loc?: Array<string>;
    msg?: string;
    line?: number;
    column?: number;
  }>;
  retry_after?: number;
};

export type PageMeta = {
  next_cursor?: string | null;
  has_more: boolean;
};

export type Role = "org_admin" | "project_owner" | "developer" | "release_captain" | "viewer";

export type Size = "S" | "M" | "L" | "XL";

export type Risk = "low" | "medium" | "high";

export type Me = {
  id: string;
  email: string;
  display_name: string;
  oidc_sub?: string;
  last_login_at?: string | null;
  memberships: Array<Membership>;
};

export type Membership = {
  user_id?: string;
  email?: string;
  org: string;
  project_slug?: string | null;
  role: Role;
};

export type MembershipUpsert = {
  email: string;
  role: Role;
  project_slug?: string | null;
};

export type Project = {
  id: string;
  slug: string;
  org: string;
  name: string;
  template_ref?: string | null;
  status: "draft" | "provisioning" | "active" | "suspended" | "archived";
  config: S.Project;
  workflow_name?: string | null;
  policy_name?: string | null;
  stats?: ProjectStats;
  created_at?: string;
  updated_at?: string;
};

export type ProjectStats = {
  active_work_items?: number;
  cost_month_eur?: number;
  budget_month_eur?: number | null;
  trains_pending?: number;
  first_pass_merge_rate?: number | null;
  cycle_time_p50_hours?: number | null;
};

export type ProjectPage = {
  items: Array<Project>;
  meta: PageMeta;
};

export type ProjectCreate = {
  slug: string;
  name: string;
  template_ref?: string | null;
  config: S.Project;
  inputs?: {
    [key: string]: unknown;
  };
};

export type ProjectUpdate = {
  name?: string;
  config?: S.Project;
};

export type ProvisionRequest = {
  inputs?: {
    [key: string]: unknown;
  };
  dry_run?: boolean;
  resume?: boolean;
};

export type ProvisionStatus = {
  project_id: string;
  workflow_id?: string | null;
  status: "pending" | "running" | "succeeded" | "failed";
  current_step?: string | null;
  steps: Array<{
    name: string;
    status: "pending" | "running" | "succeeded" | "failed" | "skipped";
    message?: string | null;
    remediation?: string | null;
    started_at?: string | null;
    ended_at?: string | null;
  }>;
};

export type Connector = {
  id?: string;
  /** une capacité (ADR 0034) : tracker, scm, ci, cd, runtime, memory, notify, gateway — ou celle qu'un type de greffon déclare (identity, mcp…) */
  kind: string;
  type: string;
  config?: {
    [key: string]: unknown;
  };
  secret_ref?: string | null;
  /** les secrets, champ par champ, en références (`env:NOM`) — jamais leur valeur (ADR 0034) */
  secret_refs?: {
    [key: string]: string;
  };
  status: "unknown" | "ok" | "degraded" | "error";
  last_check_at?: string | null;
  last_error?: string | null;
};

export type ConnectorUpsert = {
  type: string;
  /** sans champ secret : un secret en clair reçoit 422 */
  config: {
    [key: string]: unknown;
  };
  secret_ref?: string | null;
  secret_refs?: {
    [key: string]: string;
  };
};

export type ConnectorTestResult = {
  ok: boolean;
  checks: Array<{
    name: string;
    ok: boolean;
    detail?: string | null;
  }>;
};

export type ConnectorType = {
  kind: string;
  type: string;
  display: string;
  available?: boolean;
  config_schema: {
    [key: string]: unknown;
  };
  /** ce que le type sait faire : tracker, scm, identity, mcp… */
  capabilities?: Array<string>;
  /** les clefs de configuration qui sont des secrets */
  secret_fields?: Array<string>;
};

export type ConnectorOperation = {
  name: string;
  access: "read" | "write";
  policy: "allowed" | "approval" | "forbidden";
  /** les groupes de projets qui y ont droit ; aucun : tous */
  groups?: Array<string>;
  price_usd?: number | null;
  schema_digest?: string | null;
  /** le schéma d'entrée que le courtier annonce à l'agent et vérifie avant l'appel */
  input_schema?: {
    [key: string]: unknown;
  } | null;
  description?: string | null;
};

export type ConnectorDiscovery = {
  added?: Array<string>;
  changed?: Array<string>;
  removed?: Array<string>;
  unchanged?: number;
};

export type OrgConnector = {
  name: string;
  kind: string;
  type: string;
  config?: {
    [key: string]: unknown;
  };
  secret_refs?: {
    [key: string]: string;
  };
  status: string;
  last_check_at?: string | null;
  last_error?: string | null;
  created_by?: string | null;
  operations?: Array<ConnectorOperation>;
};

export type OrgConnectorCreate = {
  name: string;
  type: string;
  kind?: string | null;
  config?: {
    [key: string]: unknown;
  };
  secret_refs?: {
    [key: string]: string;
  };
};

export type OperationPatch = {
  policy?: "allowed" | "approval" | "forbidden" | null;
  groups?: Array<string> | null;
  price_usd?: number | null;
};

export type ProjectOperation = {
  connector: string;
  operation: string;
  access: string;
  org_policy: string;
  project_policy?: string | null;
  effective_policy: string;
  description?: string | null;
};

export type ProjectOperationPut = {
  policy: "allowed" | "approval" | "forbidden";
};

export type ActionEffectSpec = {
  /** un effet déclaré : connector.call, ou celui d'un greffon */
  effect: string;
  /** ses paramètres, rendus en Jinja isolé avec `params` et `effects` */
  with?: {
    [key: string]: unknown;
  };
  /** {effect, with} : ce qui le défait si une suite échoue (`result` y cite sa réponse) */
  compensate?: {
    [key: string]: unknown;
  } | null;
};

export type ActionApproval = {
  approvers?: Array<{
    role?: "developer" | "release_captain" | "project_owner" | "org_admin";
    min?: number;
  }>;
  step_up_minutes?: number;
  separation_of_duties?: boolean;
};

export type ActionCreate = {
  kind: string;
  title: string;
  justification?: string | null;
  params?: {
    [key: string]: unknown;
  };
  effects: Array<ActionEffectSpec>;
  approval?: ActionApproval;
  work_item_id?: string | null;
};

export type ActionDecision = {
  decision: "approve" | "reject";
  reason?: string | null;
};

/** Un effet sous sa clé (`<action>:<n>`) — consigné avant d'être tenté, confirmé après. */
export type ActionEffect = {
  position: number;
  key: string;
  effect: string;
  status: "started" | "done" | "compensated" | "compensation_failed";
  attempts?: number;
  result?: {
    [key: string]: unknown;
  } | null;
  error?: string | null;
  finished_at?: string | null;
};

/** Une action gouvernée (ADR 0035) et le journal de ses effets. */
export type Action = {
  id: string;
  origin: "ontology" | "tool" | "transition";
  kind: string;
  title: string;
  justification?: string | null;
  params?: {
    [key: string]: unknown;
  };
  effects?: Array<{
    [key: string]: unknown;
  }>;
  proposed_by?: {
    [key: string]: unknown;
  };
  approval?: {
    [key: string]: unknown;
  };
  decisions?: Array<{
    [key: string]: unknown;
  }>;
  status: "pending_approval" | "rejected" | "approved" | "running" | "succeeded" | "failed";
  result?: {
    [key: string]: unknown;
  } | null;
  error?: string | null;
  temporal_wf_id?: string | null;
  work_item_id?: string | null;
  run_id?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
  journal?: Array<ActionEffect>;
};

/** Une capacité que les workflows du projet exigent, et pourquoi (ADR 0034). */
export type ProjectRequirement = {
  capability: string;
  reasons: Array<string>;
  connector?: Connector | null;
  /** le type que la plateforme prend quand aucun connecteur ne la couvre */
  default_type?: string | null;
};

export type WorkflowDef = {
  id?: string;
  name: string;
  version: number;
  source: "repo" | "platform" | "template";
  yaml: string;
  json?: S.Workflow;
  checksum: string;
  is_active: boolean;
  is_default?: boolean;
  created_by?: string | null;
  created_at?: string | null;
};

export type WorkflowPut = {
  yaml: string;
  source?: "repo" | "platform" | "template";
  activate?: boolean;
  /** La version active que l'éditeur a lue : périmée → 409. */
  base_version?: number | null;
};

export type WorkflowSummary = {
  name: string;
  version: number;
  description?: string | null;
  is_default: boolean;
  /** Tickets non clos épinglés à l'une de ses versions. */
  open_items: number;
  created_by?: string | null;
  updated_at?: string | null;
};

export type WorkflowRouting = {
  default: string;
  rules: Array<{
    workflow: string;
    when: {
      labels_any?: Array<string>;
      labels_all?: Array<string>;
      item_type?: string | null;
    };
  }>;
};

export type WorkflowValidateRequest = {
  yaml?: string;
  json?: {
    [key: string]: unknown;
  };
};

export type WorkflowValidation = {
  valid: boolean;
  errors: Array<WorkflowIssue>;
  warnings: Array<WorkflowIssue>;
  graph?: WorkflowGraph;
  process?: Array<ProcessStep>;
};

/** Une transition dite en clair — la vue « processus » (ADR 0031). */
export type ProcessStep = {
  id: string;
  from: string;
  from_display?: string;
  to: string;
  to_display?: string;
  actor: string;
  actor_type: "agent" | "human" | "system" | "release_train";
  who: string;
  outputs?: Array<string>;
  gates?: Array<{
    name?: string;
    summary?: string;
  }>;
  on_fail?: string | null;
  on_reject?: string | null;
  timeout_hours?: number | null;
  sentence: string;
};

/** Une opération typée. `op` dit laquelle ; les autres champs dépendent d'elle (`name`, `spec`, `from`, `to`, `id`, `transition`, `field`, `value`, `raw`, `unset`, `gate`, `text`, `index`). */
export type WorkflowOperation = {
  op: "add_state" | "remove_state" | "rename_state" | "set_state" | "add_transition" | "remove_transition" | "set_transition" | "add_gate" | "remove_gate" | "add_actor" | "remove_actor" | "set_actor";
  [key: string]: unknown;
};

export type WorkflowEditRequest = {
  yaml: string;
  operations: Array<WorkflowOperation>;
};

export type WorkflowEditResult = WorkflowValidation & {
  yaml: string;
  /** diff unifié du texte d'origine au texte édité */
  diff: string;
  inverse: Array<WorkflowOperation>;
  /** ce qu'une opération change au-delà du texte (un état à effet renommé…) */
  notices: Array<string>;
};

export type AgentLimits = {
  max_turns?: number | null;
  max_minutes?: number | null;
};

export type AgentBudget = {
  run_usd?: number | null;
  daily_usd?: number | null;
};

/** Ce qu'une version d'agent est ; elle ne se modifie jamais (ADR 0033). */
export type AgentSpec = {
  instructions?: string;
  model?: string | null;
  backend?: string | null;
  limits?: AgentLimits;
  budget?: AgentBudget;
  skills?: Array<{
    slug: string;
    version?: number | null;
  }>;
  mcp_servers?: Array<{
    connector: string;
    tools?: Array<string>;
  }>;
};

export type AgentCreate = {
  slug: string;
  kind?: "internal" | "external";
  display_name: string;
  description?: string | null;
  spec?: AgentSpec;
};

export type AgentPatch = {
  display_name?: string | null;
  description?: string | null;
  status?: "active" | "suspended" | "revoked" | null;
  expires_at?: string | null;
};

export type AgentVersion = {
  version: number;
  spec: AgentSpec;
  checksum: string;
  created_by?: string | null;
  created_at?: string | null;
};

export type Agent = {
  slug: string;
  kind: "internal" | "external";
  display_name: string;
  description?: string | null;
  status: string;
  owner?: string | null;
  expires_at?: string | null;
  revoked_at?: string | null;
  latest_version: number;
  created_at?: string | null;
  versions?: Array<AgentVersion> | null;
};

export type AgentMetrics = {
  agent: string;
  days: number;
  runs: number;
  succeeded: number;
  failed: number;
  success_rate?: number | null;
  cost_usd?: number;
  cost_by_kind?: {
    [key: string]: number;
  };
  by_project?: Array<{
    project: string;
    runs: number;
    cost_usd: number;
  }>;
  spent_today_usd?: number;
  daily_budget_usd?: number | null;
};

export type AgentCredentialCreate = {
  kind: "token" | "oauth_client";
  token_id?: string | null;
  client_id?: string | null;
};

export type AgentCredential = {
  id: string;
  kind: string;
  token_id?: string | null;
  client_id?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  revoked_at?: string | null;
  /** le nom du jeton rattaché */
  token_name?: string | null;
  /** le dernier appel du jeton à la porte MCP : la console dit « connecté » */
  last_used_at?: string | null;
  /** le client de ce dernier appel (`User-Agent`, tronqué) */
  last_client?: string | null;
};

/** Ce qu'un projet change d'une version — seulement resserrer. */
export type AgentOverrides = {
  limits?: AgentLimits;
  budget?: AgentBudget;
  tools?: Array<string> | null;
};

export type ProjectAgentPut = {
  version: number;
  overrides?: AgentOverrides;
};

export type ProjectAgent = {
  agent: string;
  version: number;
  overrides: AgentOverrides;
  effective: AgentSpec;
};

export type SkillFiles = {
  /** chemin relatif → texte */
  files: {
    [key: string]: string;
  };
};

export type SkillVersion = {
  version: number;
  digest: string;
  created_by?: string | null;
  created_at?: string | null;
  files?: {
    [key: string]: string;
  } | null;
};

export type Skill = {
  slug: string;
  description?: string | null;
  status: string;
  latest_version: number;
  used_by?: Array<string>;
  versions?: Array<SkillVersion> | null;
};

export type WorkflowIssue = {
  code: string;
  message: string;
  path?: string | null;
  line?: number | null;
  column?: number | null;
};

export type WorkflowGraph = {
  nodes: Array<{
    id: string;
    display: string;
    kind: string;
    terminal?: boolean;
    lane?: string;
  }>;
  edges: Array<{
    id?: string | null;
    from: string;
    to: string;
    /** `nominal` pour une transition du YAML ; `reject`, `resume`, `escalate`, `default`… pour les arêtes secondaires (rendues en pointillés). */
    kind?: string;
    label?: string;
    wildcard?: boolean;
    actor?: string | null;
    actor_type?: string | null;
    gates?: Array<string>;
    via?: string | null;
  }>;
};

export type WorkflowTemplate = {
  name: string;
  version: number;
  display: string;
  description?: string;
  yaml: string;
};

export type PolicyDef = {
  id?: string;
  name: string;
  version: number;
  yaml: string;
  json?: S.Policy;
  is_active: boolean;
};

export type PolicyPut = {
  yaml: string;
  activate?: boolean;
};

export type GatewayModel = {
  model_name: string;
  litellm_model: string;
  provider?: string;
  input_cost_per_1k?: number | null;
  output_cost_per_1k?: number | null;
  max_input_tokens?: number | null;
  supports_tool_calling?: boolean;
  supports_vision?: boolean;
  internal_price?: boolean;
};

export type ProjectModels = {
  profiles: {
    [key: string]: {
      litellm_model: string;
      params?: {
        [key: string]: unknown;
      };
      max_turns_factor?: number;
    };
  };
  allow_unvalidated?: boolean;
  inherited?: {
    [key: string]: string;
  };
};

export type ModelMatrix = {
  generated_at?: string | null;
  entries: Array<{
    backend: string;
    model: string;
    validated: boolean;
    success_rate?: number | null;
    median_cost_usd?: number | null;
    median_duration_s?: number | null;
    with_memory?: boolean | null;
    report_url?: string | null;
  }>;
};

export type Org = {
  slug: string;
  name: string;
  role?: Role | null;
};

export type OrgCreate = {
  slug: string;
  name: string;
};

export type WorkItemCreate = {
  title: string;
  body?: string;
  size?: "S" | "M" | "L" | "XL" | null;
  risk?: "low" | "medium" | "high" | null;
  start?: boolean;
  /** Le workflow où le ticket naît ; sinon le routage, sinon le défaut (ADR 0031). */
  workflow?: string | null;
  /** Lues par les règles de routage. */
  labels?: Array<string>;
  /** Les champs du ticket, validés par `metadata.inputs` de son workflow. */
  fields?: {
    [key: string]: unknown;
  };
};

export type Integrations = {
  /** L'URL de la porte MCP ; celle d'un projet y ajoute `/projects/{org}:{slug}`. */
  mcp_url: string;
  protocol_versions: Array<string>;
  oauth: {
    /** Faux tant que la porte n'accepte que des jetons à portée. */
    enabled: boolean;
    authorization_server?: string | null;
  };
  version: string;
};

/** `*` : l'API REST et la CLI. `mcp:read`, `mcp:write` : la porte MCP seulement (ADR 0030) — un tel jeton est refusé par l'API REST, et la porte refuse `*`. */
export type TokenScope = "*" | "mcp:read" | "mcp:write";

export type ApiTokenCreate = {
  name: string;
  expires_in_days?: number | null;
  scopes?: Array<TokenScope>;
  /** `org:slug` : borne un jeton MCP à un seul projet. */
  project?: string | null;
};

export type ApiToken = {
  id: string;
  name: string;
  created_at: string;
  expires_at?: string | null;
  last_used_at?: string | null;
  scopes: Array<TokenScope>;
  /** `org:slug` du projet lié, s'il y en a un. */
  project?: string | null;
  /** Le client du dernier appel (`User-Agent`, tronqué). */
  last_client?: string | null;
};

export type ApiTokenCreated = ApiToken & {
  /** Le jeton en clair, rendu une seule fois. */
  token: string;
};

/** Pourquoi l'interpréteur d'un ticket est mort. Le ticket ne bougera plus sans redémarrage. */
export type WorkflowFailure = {
  message: string;
  activity?: string | null;
  state?: string | null;
  at?: string | null;
};

export type WorkItem = {
  /** Les champs du ticket (ADR 0031). */
  fields?: {
    [key: string]: unknown;
  };
  id: string;
  project_slug: string;
  tracker_key: string;
  title: string;
  body_snapshot?: string | null;
  url?: string | null;
  size?: Size | null;
  risk?: Risk | null;
  state: string;
  state_display?: string | null;
  workflow_name?: string | null;
  workflow_version?: number | null;
  temporal_wf_id?: string | null;
  /** Statut Temporal du workflow, si demandé et connu. FAILED, TERMINATED ou TIMED_OUT = le ticket est mort. */
  workflow_status?: string | null;
  failure?: WorkflowFailure | null;
  paused?: boolean;
  current_run?: RunSummary | null;
  pending_request?: HumanRequest | null;
  pr_url?: string | null;
  totals: Totals;
  estimate?: CostEstimate | null;
  created_at?: string;
  closed_at?: string | null;
};

export type Totals = {
  tokens_in?: number;
  tokens_out?: number;
  tokens_cached?: number;
  cost_usd?: number;
  cost_eur?: number;
  duration_s?: number;
  runs?: number;
};

export type CostEstimate = {
  median_usd?: number | null;
  p80_usd?: number | null;
  sample_size?: number;
  over_p80?: boolean;
};

export type WorkItemPage = {
  items: Array<WorkItem>;
  meta: PageMeta;
};

export type TimelineEntry = {
  ts: string;
  kind: "state_change" | "run" | "decision" | "finding" | "release" | "comment" | "error";
  title: string;
  detail?: string | null;
  actor?: string | null;
  actor_kind?: "user" | "agent" | "system";
  ref_id?: string | null;
  cost_usd?: number | null;
};

export type DecisionRequest = {
  request_id?: string | null;
  kind: "approve" | "reject" | "answer" | "scope_change";
  answer?: string | null;
  reason?: string | null;
  granted_paths?: Array<string>;
};

export type HumanRequest = {
  id: string;
  work_item_id?: string;
  transition_id?: string | null;
  kind: "approval" | "question" | "scope_change";
  payload?: {
    [key: string]: unknown;
  };
  requested_at: string;
  due_at?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  decision?: {
    [key: string]: unknown;
  } | null;
};

export type WorkItemAction = {
  action: "pause" | "resume" | "stop" | "migrate" | "rerun_stage" | "mark_agent_ready";
  workflow_def_id?: string | null;
  state_mapping?: {
    [key: string]: string;
  };
  run_id?: string | null;
  reason?: string | null;
};

export type RunSummary = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timed_out";
  stage_role: string;
  attempt?: number;
  backend?: string | null;
  model?: string | null;
  cost_usd?: number;
  started_at?: string | null;
};

export type Run = RunSummary & {
  work_item_id?: string;
  project_slug?: string;
  transition_id?: string | null;
  actor?: string | null;
  executor_kind?: string | null;
  executor_ref?: string | null;
  ended_at?: string | null;
  tokens?: Totals;
  gateway_key_id?: string | null;
  result?: S.StageResult | null;
  transcript_url?: string | null;
  context_pack_url?: string | null;
  playbook_checksum?: string | null;
  allowed_paths?: Array<string>;
};

export type RunEvent = {
  seq: number;
  type: string;
  ts: string;
  payload?: {
    [key: string]: unknown;
  };
};

export type RunEventIn = {
  seq: number;
  type: string;
  ts?: string | null;
  payload?: {
    [key: string]: unknown;
  };
};

export type ArtifactRef = {
  url: string;
  content_type?: string;
  size_bytes?: number | null;
  inline?: string | null;
};

export type DiffSummary = {
  base?: string | null;
  head?: string | null;
  files: Array<{
    path: string;
    status: "added" | "modified" | "removed" | "renamed";
    additions: number;
    deletions: number;
    patch?: string | null;
    in_scope?: boolean;
  }>;
  additions: number;
  deletions: number;
};

export type Release = {
  id: string;
  project_slug: string;
  env: string;
  batch_no: number;
  status: "collecting" | "departing" | "staging" | "awaiting_approval" | "promoting" | "verifying" | "done" | "rolled_back" | "frozen";
  items: Array<{
    work_item_key: string;
    title?: string | null;
    sha: string;
    pr_url?: string | null;
    risk?: Risk | null;
    labels?: Array<string>;
  }>;
  started_at?: string | null;
  ended_at?: string | null;
  approved_by?: string | null;
  verdict?: {
    [key: string]: unknown;
  } | null;
  notes?: string | null;
  promotion_url?: string | null;
};

export type ReleasePage = {
  items: Array<Release>;
  meta: PageMeta;
};

export type TrainStatus = {
  env: string;
  status: string;
  batch_size: number;
  pending_items?: Array<string>;
  next_departure?: string | null;
  frozen: boolean;
  freeze_reason?: string | null;
  current_release?: Release | null;
  window_open?: boolean;
};

export type FindingRecord = {
  id: string;
  project_slug: string;
  origin_work_item_key?: string | null;
  origin_run_id?: string | null;
  title: string;
  type: string;
  severity: "low" | "medium" | "high" | "critical";
  evidence?: string;
  suggested_fix?: string | null;
  estimate?: string | null;
  status: "pending" | "created" | "duplicate" | "dismissed";
  created_work_item_key?: string | null;
  duplicate_of?: string | null;
  occurrences?: number;
  relevant?: boolean | null;
  created_at?: string;
};

export type FindingPage = {
  items: Array<FindingRecord>;
  meta: PageMeta;
};

export type FindingAction = {
  action: "create_ticket" | "mark_duplicate" | "dismiss" | "agent_ready" | "mark_relevant" | "mark_false_positive";
  duplicate_of?: string | null;
  reason?: string | null;
};

export type FindingAck = {
  accepted: boolean;
  finding_id?: string | null;
  remaining?: number;
};

export type ScopeChangeDecision = {
  decision: "granted" | "pending" | "denied";
  allowed_paths?: Array<string>;
  reason?: string | null;
};

export type Memory = {
  id?: string;
  kind: string;
  subject: string;
  content: string;
  score?: number | null;
  status?: "active" | "pending" | "superseded" | "rejected";
  valid_from?: string | null;
  valid_to?: string | null;
  provenance?: {
    [key: string]: unknown;
  };
  proposed_by?: string | null;
};

export type MemoryDecision = {
  id: string;
  action: "accept" | "reject";
  reason?: string | null;
};

export type CostReport = {
  group_by: string;
  currency?: string;
  rows: Array<{
    key: string;
    cost_usd: number;
    cost_eur: number;
    tokens_in?: number;
    tokens_out?: number;
    tokens_cached?: number;
    runs?: number;
  }>;
  total: {
    cost_usd?: number;
    cost_eur?: number;
    budget_usd?: number | null;
  };
};

export type MemoryAbGroup = {
  projects?: Array<string>;
  tickets?: number;
  first_pass_merge_rate?: number;
  cost_per_ticket_usd?: number;
};

export type MemoryAbReport = {
  org: string;
  weeks?: number;
  since?: string;
  with_memory?: MemoryAbGroup;
  without_memory?: MemoryAbGroup;
  verdict: string;
  detail?: string;
};

export type CrossBackendArm = {
  reviews?: number;
  caught?: number;
  catch_rate?: number;
  backends?: Array<string>;
};

export type CrossBackendReport = {
  since: string;
  cross_backend_required?: boolean;
  same_backend?: CrossBackendArm;
  other_backend?: CrossBackendArm;
  verdict: string;
  detail?: string;
};

export type DoraMetric = {
  value?: number;
  unit: string;
  level?: "elite" | "high" | "medium" | "low" | "unknown";
  sample?: number;
};

export type DoraReport = {
  env?: string;
  since: string;
  until: string;
  deployments?: number;
  deployment_frequency: DoraMetric;
  lead_time: DoraMetric;
  change_failure_rate: DoraMetric;
  time_to_restore: DoraMetric;
};

export type TemplateSummary = {
  name: string;
  version: string;
  display: string;
  description?: string | null;
  repo_url?: string | null;
  is_published: boolean;
};

export type TemplateDetail = TemplateSummary & {
  manifest: S.Template;
};

export type TemplateUpsert = {
  manifest: S.Template;
  repo_url?: string | null;
  is_published?: boolean;
};

export type AgentBackendInfo = {
  name: string;
  enabled: boolean;
  version?: string | null;
  capabilities: Array<string>;
  conformance?: {
    last_run_at?: string | null;
    passed?: number | null;
    total?: number | null;
    report_url?: string | null;
    failures?: Array<string>;
  };
  disabled_reason?: string | null;
};

export type AgentBackendUpdate = {
  name: string;
  enabled: boolean;
  disabled_reason?: string | null;
};

export type ExecutorInfo = {
  kind: "tekton" | "k8s_job" | "local_docker" | "aca";
  enabled: boolean;
  cluster?: string | null;
  namespace_pattern?: string | null;
  runner_image?: string | null;
  default_timeout_minutes?: number;
};

export type GatewayKeyInfo = {
  key_id: string;
  run_id?: string | null;
  project_slug?: string | null;
  budget_usd?: number | null;
  spend_usd?: number;
  expires_at?: string | null;
  revoked?: boolean;
  created_at: string;
};

export type AuditEntry = {
  id: string;
  actor_id?: string | null;
  actor_kind: "user" | "agent" | "system";
  action: string;
  target_type?: string | null;
  target_id?: string | null;
  payload?: {
    [key: string]: unknown;
  };
  ts: string;
};

export type AuditPage = {
  items: Array<AuditEntry>;
  meta: PageMeta;
};

export type WebhookAck = {
  accepted: boolean;
  duplicate?: boolean;
  events?: number;
};

export type RunTicket = {
  key: string;
  title: string;
  body?: string;
  /** Les champs du ticket (ADR 0031). */
  fields?: {
    [key: string]: unknown;
  };
  url?: string | null;
  spec_markdown?: string | null;
  plan_markdown?: string | null;
  comments?: Array<{
    author?: string;
    body?: string;
    ts?: string;
  }>;
};

export type StageInput = S.StageInput;

export type StageResult = S.StageResult;

export type ContextPack = S.ContextPack;

export type Finding = S.Finding;

export type InboundEvent = S.InboundEvent;

export type ChoregosEvent = S.Event;

export type HumanDecision = S.HumanDecision;

export interface Operations {
  abortRelease: { method: "POST"; path: "/releases/{id}/abort"; body: {
  reason?: string;
}; response: void };
  addMember: { method: "POST"; path: "/orgs/{org}/members"; body: MembershipUpsert; response: Membership };
  alertmanagerWebhook: { method: "POST"; path: "/webhooks/alertmanager"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  approveRelease: { method: "POST"; path: "/releases/{id}/approve"; body: {
  note?: string;
}; response: void };
  argocdWebhook: { method: "POST"; path: "/webhooks/argocd"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  attachAgentCredential: { method: "POST"; path: "/orgs/{org}/agents/{slug}/credentials"; body: AgentCredentialCreate; response: AgentCredential };
  authCallback: { method: "GET"; path: "/auth/callback"; body: never; response: void };
  authLogin: { method: "GET"; path: "/auth/login"; body: never; response: void };
  authLogout: { method: "POST"; path: "/auth/logout"; body: never; response: void };
  callRunTool: { method: "POST"; path: "/internal/runs/{id}/tools/{name}"; body: {
  [key: string]: unknown;
}; response: {
  status_code: number;
  result: unknown;
  remaining?: number;
} };
  createAgent: { method: "POST"; path: "/orgs/{org}/agents"; body: AgentCreate; response: Agent };
  createMyToken: { method: "POST"; path: "/me/tokens"; body: ApiTokenCreate; response: ApiTokenCreated };
  createOrg: { method: "POST"; path: "/orgs"; body: OrgCreate; response: Org };
  createOrgConnector: { method: "POST"; path: "/orgs/{org}/connectors"; body: OrgConnectorCreate; response: OrgConnector };
  createProject: { method: "POST"; path: "/orgs/{org}/projects"; body: ProjectCreate; response: Project };
  createSkill: { method: "POST"; path: "/orgs/{org}/skills"; body: SkillFiles; response: Skill };
  createTemplate: { method: "POST"; path: "/templates"; body: TemplateUpsert; response: TemplateSummary };
  createWorkItem: { method: "POST"; path: "/projects/{id}/work-items"; body: WorkItemCreate; response: WorkItem };
  deactivateWorkflow: { method: "POST"; path: "/projects/{id}/workflows/{name}/deactivate"; body: never; response: void };
  decideAction: { method: "POST"; path: "/projects/{id}/actions/{action_id}/decision"; body: ActionDecision; response: Action };
  decidePendingMemory: { method: "POST"; path: "/projects/{id}/memory/pending"; body: MemoryDecision; response: void };
  deleteOrgConnector: { method: "DELETE"; path: "/orgs/{org}/connectors/{name}"; body: never; response: void };
  deleteProject: { method: "DELETE"; path: "/projects/{id}"; body: never; response: void };
  departTrain: { method: "POST"; path: "/projects/{id}/trains/{env}/depart"; body: never; response: void };
  detachAgentCredential: { method: "DELETE"; path: "/orgs/{org}/agents/{slug}/credentials/{credential_id}"; body: never; response: void };
  discoverConnectorOperations: { method: "POST"; path: "/orgs/{org}/connectors/{name}/discover"; body: never; response: ConnectorDiscovery };
  editWorkflow: { method: "POST"; path: "/workflows/edit"; body: WorkflowEditRequest; response: WorkflowEditResult };
  edition: { method: "GET"; path: "/edition"; body: never; response: {
  edition: "community" | "enterprise";
  features: Array<string>;
  version: string;
} };
  exportProjectCostsCsv: { method: "GET"; path: "/projects/{id}/costs.csv"; body: never; response: void };
  freezeTrain: { method: "POST"; path: "/projects/{id}/trains/{env}/freeze"; body: {
  reason: string;
}; response: void };
  getAction: { method: "GET"; path: "/projects/{id}/actions/{action_id}"; body: never; response: Action };
  getAgent: { method: "GET"; path: "/orgs/{org}/agents/{slug}"; body: never; response: Agent };
  getAgentMetrics: { method: "GET"; path: "/orgs/{org}/agents/{slug}/metrics"; body: never; response: AgentMetrics };
  getAgentVersion: { method: "GET"; path: "/orgs/{org}/agents/{slug}/versions/{version}"; body: never; response: AgentVersion };
  getIntegrations: { method: "GET"; path: "/integrations"; body: never; response: Integrations };
  getMe: { method: "GET"; path: "/me"; body: never; response: Me };
  getMemoryAbReport: { method: "GET"; path: "/orgs/{org}/memory/ab-report"; body: never; response: MemoryAbReport };
  getModelMatrix: { method: "GET"; path: "/projects/{id}/models/matrix"; body: never; response: ModelMatrix };
  getNamedWorkflow: { method: "GET"; path: "/projects/{id}/workflows/{name}"; body: never; response: WorkflowDef };
  getOrgConnector: { method: "GET"; path: "/orgs/{org}/connectors/{name}"; body: never; response: OrgConnector };
  getOrgCosts: { method: "GET"; path: "/orgs/{org}/costs"; body: never; response: CostReport };
  getPolicy: { method: "GET"; path: "/projects/{id}/policy"; body: never; response: PolicyDef };
  getProject: { method: "GET"; path: "/projects/{id}"; body: never; response: Project };
  getProjectCosts: { method: "GET"; path: "/projects/{id}/costs"; body: never; response: CostReport };
  getProjectCrossBackend: { method: "GET"; path: "/projects/{id}/metrics/cross-backend"; body: never; response: CrossBackendReport };
  getProjectDora: { method: "GET"; path: "/projects/{id}/metrics/dora"; body: never; response: DoraReport };
  getProjectModels: { method: "GET"; path: "/projects/{id}/models"; body: never; response: ProjectModels };
  getProjectTools: { method: "GET"; path: "/projects/{id}/tools"; body: never; response: {
  allows_all: boolean;
  tools: Array<{
    name: string;
    description: string;
    provider: string;
    categories?: Array<string>;
    price_eur?: number;
    needs_credential?: boolean;
    source?: "http" | "mcp";
    groups?: Array<string>;
    allowed: boolean;
  }>;
} };
  getProvisionStatus: { method: "GET"; path: "/projects/{id}/provision"; body: never; response: ProvisionStatus };
  getRelease: { method: "GET"; path: "/releases/{id}"; body: never; response: Release };
  getRun: { method: "GET"; path: "/runs/{id}"; body: never; response: Run };
  getRunAccess: { method: "GET"; path: "/runs/{id}/access"; body: never; response: {
  /** Événements du journal parcourus */
  evenements: number;
  /** Nombre total de demandes refusées */
  refus: number;
  cout_outils_eur?: number;
  acces: Array<{
    nature: "read" | "write" | "execute" | "network" | "tool" | "autre";
    cible: string;
    demandes: number;
    refus: number;
    motifs?: Array<string>;
  }>;
} };
  getRunCiLogs: { method: "GET"; path: "/internal/runs/{id}/ci-logs"; body: never; response: {
  logs?: string;
  ref?: string;
} };
  getRunContext: { method: "GET"; path: "/internal/runs/{id}/context"; body: never; response: ContextPack };
  getRunDiff: { method: "GET"; path: "/runs/{id}/diff"; body: never; response: DiffSummary };
  getRunEvents: { method: "GET"; path: "/runs/{id}/events"; body: never; response: Array<RunEvent> };
  getRunInput: { method: "GET"; path: "/internal/runs/{id}/input"; body: never; response: StageInput };
  getRunSkills: { method: "GET"; path: "/internal/runs/{id}/skills"; body: never; response: Array<{
  slug: string;
  version: number;
  digest: string;
  files: {
    [key: string]: string;
  };
}> };
  getRunTicket: { method: "GET"; path: "/internal/runs/{id}/ticket"; body: never; response: RunTicket };
  getRunTools: { method: "GET"; path: "/internal/runs/{id}/tools"; body: never; response: {
  tools?: Array<{
    name: string;
    description: string;
    inputSchema?: {
      [key: string]: unknown;
    };
  }>;
} };
  getRunTranscript: { method: "GET"; path: "/runs/{id}/transcript"; body: never; response: ArtifactRef };
  getSkill: { method: "GET"; path: "/orgs/{org}/skills/{slug}"; body: never; response: Skill };
  getSkillVersion: { method: "GET"; path: "/orgs/{org}/skills/{slug}/versions/{version}"; body: never; response: SkillVersion };
  getTemplate: { method: "GET"; path: "/templates/{name}"; body: never; response: TemplateDetail };
  getTrainStatus: { method: "GET"; path: "/projects/{id}/trains/{env}"; body: never; response: TrainStatus };
  getWorkItem: { method: "GET"; path: "/work-items/{id}"; body: never; response: WorkItem };
  getWorkItemTimeline: { method: "GET"; path: "/work-items/{id}/timeline"; body: never; response: Array<TimelineEntry> };
  getWorkflow: { method: "GET"; path: "/projects/{id}/workflow"; body: never; response: WorkflowDef };
  getWorkflowRouting: { method: "GET"; path: "/projects/{id}/workflow-routing"; body: never; response: WorkflowRouting };
  githubWebhook: { method: "POST"; path: "/webhooks/github"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  gitlabWebhook: { method: "POST"; path: "/webhooks/gitlab"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  healthz: { method: "GET"; path: "/healthz"; body: never; response: void };
  importSkill: { method: "POST"; path: "/orgs/{org}/skills/import"; body: void; response: Skill };
  jiraWebhook: { method: "POST"; path: "/webhooks/jira"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  listActions: { method: "GET"; path: "/projects/{id}/actions"; body: never; response: Array<Action> };
  listAdminSections: { method: "GET"; path: "/ui/admin-sections"; body: never; response: Array<S.UiManifest> };
  listAgentCredentials: { method: "GET"; path: "/orgs/{org}/agents/{slug}/credentials"; body: never; response: Array<AgentCredential> };
  listAgents: { method: "GET"; path: "/orgs/{org}/agents"; body: never; response: Array<Agent> };
  listAudit: { method: "GET"; path: "/audit"; body: never; response: AuditPage };
  listBackends: { method: "GET"; path: "/platform/backends"; body: never; response: Array<AgentBackendInfo> };
  listConnectorTypes: { method: "GET"; path: "/connectors/types"; body: never; response: Array<ConnectorType> };
  listConnectors: { method: "GET"; path: "/projects/{id}/connectors"; body: never; response: Array<Connector> };
  listExecutors: { method: "GET"; path: "/platform/executors"; body: never; response: Array<ExecutorInfo> };
  listFindings: { method: "GET"; path: "/projects/{id}/findings"; body: never; response: FindingPage };
  listGatewayKeys: { method: "GET"; path: "/platform/gateway/keys"; body: never; response: Array<GatewayKeyInfo> };
  listMembers: { method: "GET"; path: "/orgs/{org}/members"; body: never; response: Array<Membership> };
  listMyTokens: { method: "GET"; path: "/me/tokens"; body: never; response: Array<ApiToken> };
  listOrgConnectors: { method: "GET"; path: "/orgs/{org}/connectors"; body: never; response: Array<OrgConnector> };
  listOrgs: { method: "GET"; path: "/orgs"; body: never; response: Array<Org> };
  listPendingMemory: { method: "GET"; path: "/projects/{id}/memory/pending"; body: never; response: Array<Memory> };
  listPlatformModels: { method: "GET"; path: "/platform/models"; body: never; response: Array<GatewayModel> };
  listProjectAgents: { method: "GET"; path: "/projects/{id}/agents"; body: never; response: Array<ProjectAgent> };
  listProjectOperations: { method: "GET"; path: "/projects/{id}/operations"; body: never; response: Array<ProjectOperation> };
  listProjectRequirements: { method: "GET"; path: "/projects/{id}/requirements"; body: never; response: Array<ProjectRequirement> };
  listProjects: { method: "GET"; path: "/orgs/{org}/projects"; body: never; response: ProjectPage };
  listReleases: { method: "GET"; path: "/projects/{id}/releases"; body: never; response: ReleasePage };
  listRuns: { method: "GET"; path: "/work-items/{id}/runs"; body: never; response: Array<Run> };
  listSkills: { method: "GET"; path: "/orgs/{org}/skills"; body: never; response: Array<Skill> };
  listTemplates: { method: "GET"; path: "/templates"; body: never; response: Array<TemplateSummary> };
  listWorkItems: { method: "GET"; path: "/projects/{id}/work-items"; body: never; response: WorkItemPage };
  listWorkflowTemplates: { method: "GET"; path: "/workflows/templates"; body: never; response: Array<WorkflowTemplate> };
  listWorkflowVersions: { method: "GET"; path: "/projects/{id}/workflows/{name}/versions"; body: never; response: Array<WorkflowDef> };
  listWorkflows: { method: "GET"; path: "/projects/{id}/workflows"; body: never; response: Array<WorkflowSummary> };
  pinProjectAgent: { method: "PUT"; path: "/projects/{id}/agents/{slug}"; body: ProjectAgentPut; response: ProjectAgent };
  postDecision: { method: "POST"; path: "/work-items/{id}/decisions"; body: DecisionRequest; response: HumanRequest };
  postFindingAction: { method: "POST"; path: "/findings/{id}/actions"; body: FindingAction; response: FindingRecord };
  postRunEvents: { method: "POST"; path: "/internal/runs/{id}/events"; body: {
  events: Array<RunEventIn>;
}; response: void };
  postRunFinding: { method: "POST"; path: "/internal/runs/{id}/findings"; body: Finding; response: FindingAck };
  postRunQuestion: { method: "POST"; path: "/internal/runs/{id}/question"; body: {
  text: string;
  options?: Array<string>;
}; response: void };
  postRunResult: { method: "POST"; path: "/internal/runs/{id}/result"; body: StageResult; response: void };
  postScopeChange: { method: "POST"; path: "/internal/runs/{id}/scope-change"; body: {
  paths: Array<string>;
  justification: string;
}; response: ScopeChangeDecision };
  postWorkItemAction: { method: "POST"; path: "/work-items/{id}/actions"; body: WorkItemAction; response: void };
  proposeAction: { method: "POST"; path: "/projects/{id}/actions"; body: ActionCreate; response: Action };
  provisionProject: { method: "POST"; path: "/projects/{id}/provision"; body: ProvisionRequest; response: ProvisionStatus };
  publishAgentVersion: { method: "POST"; path: "/orgs/{org}/agents/{slug}/versions"; body: AgentSpec; response: AgentVersion };
  publishSkillVersion: { method: "POST"; path: "/orgs/{org}/skills/{slug}/versions"; body: SkillFiles; response: SkillVersion };
  putBackend: { method: "PUT"; path: "/platform/backends"; body: AgentBackendUpdate; response: AgentBackendInfo };
  putConnector: { method: "PUT"; path: "/projects/{id}/connectors/{kind}"; body: ConnectorUpsert; response: Connector };
  putExecutor: { method: "PUT"; path: "/platform/executors"; body: ExecutorInfo; response: ExecutorInfo };
  putNamedWorkflow: { method: "PUT"; path: "/projects/{id}/workflows/{name}"; body: WorkflowPut; response: WorkflowDef };
  putPolicy: { method: "PUT"; path: "/projects/{id}/policy"; body: PolicyPut; response: PolicyDef };
  putProjectModels: { method: "PUT"; path: "/projects/{id}/models"; body: ProjectModels; response: ProjectModels };
  putWorkflow: { method: "PUT"; path: "/projects/{id}/workflow"; body: WorkflowPut; response: WorkflowDef };
  putWorkflowRouting: { method: "PUT"; path: "/projects/{id}/workflow-routing"; body: WorkflowRouting; response: WorkflowRouting };
  readyz: { method: "GET"; path: "/readyz"; body: never; response: void };
  reimportMemory: { method: "POST"; path: "/projects/{id}/memory/reimport"; body: {
  sources?: Array<string>;
}; response: void };
  relaxProjectOperation: { method: "DELETE"; path: "/projects/{id}/operations/{connector}/{operation}"; body: never; response: void };
  removeMember: { method: "DELETE"; path: "/orgs/{org}/members/{user_id}"; body: never; response: void };
  restoreWorkflowVersion: { method: "POST"; path: "/projects/{id}/workflows/{name}/versions/{version}/restore"; body: never; response: WorkflowDef };
  revokeMyToken: { method: "DELETE"; path: "/me/tokens/{id}"; body: never; response: void };
  searchMemory: { method: "GET"; path: "/projects/{id}/memory/search"; body: never; response: Array<Memory> };
  suspendProject: { method: "POST"; path: "/projects/{id}/suspend"; body: never; response: Project };
  tektonWebhook: { method: "POST"; path: "/webhooks/tekton"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  testConnector: { method: "POST"; path: "/projects/{id}/connectors/{kind}/test"; body: never; response: ConnectorTestResult };
  tightenProjectOperation: { method: "PUT"; path: "/projects/{id}/operations/{connector}/{operation}"; body: ProjectOperationPut; response: ProjectOperation };
  unfreezeTrain: { method: "POST"; path: "/projects/{id}/trains/{env}/unfreeze"; body: never; response: void };
  unpinProjectAgent: { method: "DELETE"; path: "/projects/{id}/agents/{slug}"; body: never; response: void };
  updateAgent: { method: "PATCH"; path: "/orgs/{org}/agents/{slug}"; body: AgentPatch; response: Agent };
  updateConnectorOperation: { method: "PATCH"; path: "/orgs/{org}/connectors/{name}/operations/{operation}"; body: OperationPatch; response: ConnectorOperation };
  updateProject: { method: "PATCH"; path: "/projects/{id}"; body: ProjectUpdate; response: Project };
  updateTemplate: { method: "PUT"; path: "/templates/{name}"; body: TemplateUpsert; response: TemplateSummary };
  validateWorkflow: { method: "POST"; path: "/workflows/validate"; body: WorkflowValidateRequest; response: WorkflowValidation };
}

export type OperationId = keyof Operations;
