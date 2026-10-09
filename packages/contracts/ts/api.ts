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
  /** a capability (ADR 0034): tracker, scm, ci, cd, runtime, memory, notify, gateway — or one a plugin type declares (identity, mcp…) */
  kind: string;
  type: string;
  config?: {
    [key: string]: unknown;
  };
  secret_ref?: string | null;
  /** the secrets, field by field, as references (`env:NAME`) — never their value (ADR 0034) */
  secret_refs?: {
    [key: string]: string;
  };
  status: "unknown" | "ok" | "degraded" | "error";
  last_check_at?: string | null;
  last_error?: string | null;
};

export type ConnectorUpsert = {
  type: string;
  /** no secret field: a plaintext secret gets 422 */
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
  /** what the type can do: tracker, scm, identity, mcp… */
  capabilities?: Array<string>;
  /** the configuration keys that are secrets */
  secret_fields?: Array<string>;
};

export type ConnectorOperation = {
  name: string;
  access: "read" | "write";
  policy: "allowed" | "approval" | "forbidden";
  /** the project groups entitled to it; none: all of them */
  groups?: Array<string>;
  price_usd?: number | null;
  schema_digest?: string | null;
  /** the input schema the broker announces to the agent and checks before the call */
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
  /** a declared effect: connector.call, or a plugin's */
  effect: string;
  /** its parameters, rendered in sandboxed Jinja with `params` and `effects` */
  with?: {
    [key: string]: unknown;
  };
  /** {effect, with}: what undoes it if a later effect fails (`result` quotes its response there) */
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

/** An effect under its key (`<action>:<n>`) — recorded before it is attempted, confirmed after. */
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

/** A governed action (ADR 0035) and the log of its effects. */
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
  status: "pending_approval" | "rejected" | "approved" | "running" | "awaiting_evidence" | "succeeded" | "failed";
  result?: {
    [key: string]: unknown;
  } | null;
  error?: string | null;
  temporal_wf_id?: string | null;
  work_item_id?: string | null;
  run_id?: string | null;
  project_slug?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
  journal?: Array<ActionEffect>;
};

/** A capability the project's workflows require, and why (ADR 0034). */
export type ProjectRequirement = {
  capability: string;
  reasons: Array<string>;
  connector?: Connector | null;
  /** the type the platform uses when no connector covers it */
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
  /** The active version the editor read: stale → 409. */
  base_version?: number | null;
  /** Create a new workflow: a workflow of that name already exists (active or not) → 409, nothing is published. */
  create_only?: boolean;
};

export type WorkflowSummary = {
  name: string;
  version: number;
  description?: string | null;
  is_default: boolean;
  /** Open tickets pinned to one of its versions. */
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

/** A transition in plain words — the process view (ADR 0031). */
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

/** A typed operation. `op` says which one; the other fields depend on it (`name`, `spec`, `from`, `to`, `id`, `transition`, `field`, `value`, `raw`, `unset`, `gate`, `text`, `index`). */
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
  /** unified diff from the original text to the edited text */
  diff: string;
  inverse: Array<WorkflowOperation>;
  /** what an operation changes beyond the text (a state with effects renamed…) */
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

/** What an agent version is; it never changes (ADR 0033). */
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

/** An agent the platform's catalogue offers (ADR 0040), and its state in the organisation. */
export type AgentCatalogueEntry = {
  slug: string;
  kind: "internal" | "external";
  display_name: string;
  description?: string | null;
  /** the package role it plays (`implement`, `review`…), or `external` for a client */
  role: string;
  summary: string;
  /** the catalogue's version of the entry */
  version: number;
  skills?: Array<string>;
  /** an external client's id on the AI clients page */
  client?: string | null;
  /** `cloud`: it calls from its vendor's cloud */
  reach?: "always" | "cloud" | null;
  installed: boolean;
  installed_version?: number | null;
  /** an agent of the organisation has this name and does not come from the catalogue */
  own_agent?: boolean;
  update_available?: boolean;
  /** false for a client the gate does not accept yet (OAuth, registered client, https) */
  offered?: boolean;
  unavailable_reason?: string | null;
};

export type AgentCatalogueConnect = {
  /** a `mcp:read` token instead of `mcp:write` */
  read_only?: boolean;
  expires_in_days?: number | null;
};

export type AgentCatalogueConnection = {
  agent: Agent;
  /** returned once; none for a client that signs in with OAuth */
  token?: ApiTokenCreated | null;
  oauth_client_id?: string | null;
};

export type AgentCatalogueInstall = {
  /** already installed: publish the catalogue's version as the next one */
  upgrade?: boolean;
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
  /** the name of the attached token */
  token_name?: string | null;
  /** the token's last call to the MCP gate: the console shows it as connected */
  last_used_at?: string | null;
  /** the client of that last call (`User-Agent`, truncated) */
  last_client?: string | null;
};

/** What a project changes in a version — tightening only. */
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
  /** relative path → text */
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
    /** The tracker column the state maps to; the console groups the path into stages by it. */
    tracker_status?: string | null;
  }>;
  edges: Array<{
    id?: string | null;
    from: string;
    to: string;
    /** `nominal` for a transition of the YAML; `reject`, `resume`, `escalate`, `default`… for the secondary edges (drawn dashed). */
    kind?: string;
    label?: string;
    wildcard?: boolean;
    actor?: string | null;
    actor_type?: string | null;
    role?: string | null;
    gates?: Array<string>;
    via?: string | null;
    timeout_hours?: number | null;
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

/** What changes in an organisation; the slug is in every URL and every MCP address. */
export type OrgUpdate = {
  slug?: string | null;
  name?: string | null;
};

export type WorkItemCreate = {
  title: string;
  body?: string;
  size?: "S" | "M" | "L" | "XL" | null;
  risk?: "low" | "medium" | "high" | null;
  start?: boolean;
  /** The workflow the ticket starts in; otherwise the routing, otherwise the default one (ADR 0031). */
  workflow?: string | null;
  /** Read by the routing rules. */
  labels?: Array<string>;
  /** The ticket's fields, validated by its workflow's `metadata.inputs`. */
  fields?: {
    [key: string]: unknown;
  };
};

export type WorkItemUpdate = {
  /** The fields that change; `null` removes one. */
  fields: {
    [key: string]: unknown;
  };
};

export type Integrations = {
  /** The MCP gate's URL; a project's adds `/projects/{org}:{slug}` to it. */
  mcp_url: string;
  protocol_versions: Array<string>;
  oauth: {
    /** False as long as the gate accepts only scoped tokens. */
    enabled: boolean;
    authorization_server?: string | null;
    /** The clients the IdP registered for the gate, by client of the Integrations page (`claude-code`, `claude-ai`…): the page derives the exact command from them. */
    clients?: {
      [key: string]: {
        client_id: string;
        /** The port of the local redirect `http://localhost:PORT/callback` registered with the IdP. */
        callback_port?: number | null;
      };
    };
  };
  version: string;
};

/** `*`: the REST API and the CLI. `mcp:read`, `mcp:write`: the MCP gate only (ADR 0030) — such a token is refused by the REST API, and the gate refuses `*`. */
export type TokenScope = "*" | "mcp:read" | "mcp:write";

export type ApiTokenCreate = {
  name: string;
  expires_in_days?: number | null;
  scopes?: Array<TokenScope>;
  /** `org:slug`: bounds an MCP token to a single project. */
  project?: string | null;
};

export type ApiToken = {
  id: string;
  name: string;
  created_at: string;
  expires_at?: string | null;
  last_used_at?: string | null;
  scopes: Array<TokenScope>;
  /** `org:slug` of the linked project, if there is one. */
  project?: string | null;
  /** The client of the last call (`User-Agent`, truncated). */
  last_client?: string | null;
};

export type ApiTokenCreated = ApiToken & {
  /** The plaintext token, returned only once. */
  token: string;
};

/** Why a ticket's interpreter died. The ticket will not move again without a restart. */
export type WorkflowFailure = {
  message: string;
  activity?: string | null;
  state?: string | null;
  at?: string | null;
};

export type WorkItem = {
  /** The ticket's fields (ADR 0031). */
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
  /** The workflow's Temporal status, when requested and known. FAILED, TERMINATED or TIMED_OUT = the ticket is dead. */
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

export type GitToken = {
  token: string | null;
  expires_at?: string | null;
  /** `owner/name`: the only repository the token opens. */
  repository?: string | null;
};

/** A work item's path through its workflow (S22-01). `graph` and `process` describe the version the item is pinned to, not the project's current one; `moves` and `steps` are in time order. */
export type WorkItemJourney = {
  work_item_id: string;
  tracker_key?: string | null;
  workflow_name?: string | null;
  workflow_version?: number | null;
  /** The workflow's initial state. */
  initial?: string | null;
  /** The state the item is in now. */
  state: string;
  closed?: boolean;
  graph: WorkflowGraph;
  process: Array<ProcessStep>;
  moves: Array<JourneyMove>;
  steps: Array<JourneyStep>;
};

/** The item left `from` for `to`. `transition_id` and `kind` say which edge of the graph carried it — `nominal` (the transition itself), `reject`, `changes_requested`, `retry` (a failure sent it back), `escalate`, `default` (from any agent state), `resume`, `start`, `migrated`, or `other` when the graph has no such edge. A retry that keeps the item in its state is not a move: it shows as a second step on the same transition. */
export type JourneyMove = {
  at: string;
  from?: string | null;
  to: string;
  kind: "start" | "nominal" | "reject" | "changes_requested" | "retry" | "escalate" | "default" | "resume" | "migrated" | "other";
  transition_id?: string | null;
  reason?: string | null;
};

/** One thing an actor did for the item, on one transition: an agent run, a request to a person, or a governed action. `status` is the run's, the request's (`waiting`, `approved`, `rejected`, `answered`, `completed`, `scope_change`) or the action's. */
export type JourneyStep = {
  /** The run, request or action id: the one its own page reads. */
  id: string;
  kind: "agent" | "human" | "action";
  transition_id?: string | null;
  actor?: string | null;
  role?: string | null;
  attempt: number;
  status: string;
  started_at?: string | null;
  ended_at?: string | null;
  summary?: string | null;
  /** The `verdict` output of a review, when the step wrote one. */
  verdict?: string | null;
  cost_usd?: number;
  model?: string | null;
  decided_by?: string | null;
  due_at?: string | null;
  /** What the platform measured for an agent run (tests, lint, coverage…). */
  evidence?: {
    [key: string]: unknown;
  };
};

export type DecisionRequest = {
  request_id?: string | null;
  kind: "approve" | "reject" | "answer" | "scope_change" | "complete";
  answer?: string | null;
  reason?: string | null;
  granted_paths?: Array<string>;
  /** `complete`: the values of the task's form, written into the ticket's fields. */
  values?: {
    [key: string]: unknown;
  };
  /** `complete`: the person attests the task's statement. */
  attested?: boolean;
};

export type HumanRequest = {
  id: string;
  work_item_id?: string;
  transition_id?: string | null;
  kind: "approval" | "question" | "scope_change" | "task";
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
  /** The ticket's fields (ADR 0031). */
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
  connectCatalogueClient: { method: "POST"; path: "/orgs/{org}/agent-catalogue/{slug}/connect"; body: AgentCatalogueConnect; response: AgentCatalogueConnection };
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
  /** Log events scanned */
  evenements: number;
  /** Total number of denied requests */
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
  getWorkItemJourney: { method: "GET"; path: "/work-items/{id}/journey"; body: never; response: WorkItemJourney };
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
  installCatalogueAgent: { method: "POST"; path: "/orgs/{org}/agent-catalogue/{slug}/install"; body: AgentCatalogueInstall; response: Agent };
  jiraWebhook: { method: "POST"; path: "/webhooks/jira"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  listActions: { method: "GET"; path: "/projects/{id}/actions"; body: never; response: Array<Action> };
  listAdminSections: { method: "GET"; path: "/ui/admin-sections"; body: never; response: Array<S.UiManifest> };
  listAgentCatalogue: { method: "GET"; path: "/orgs/{org}/agent-catalogue"; body: never; response: Array<AgentCatalogueEntry> };
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
  listOrgActions: { method: "GET"; path: "/orgs/{org}/actions"; body: never; response: Array<Action> };
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
  postRunGitToken: { method: "POST"; path: "/internal/runs/{id}/git-token"; body: never; response: GitToken };
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
  updateOrg: { method: "PATCH"; path: "/orgs/{org}"; body: OrgUpdate; response: Org };
  updateProject: { method: "PATCH"; path: "/projects/{id}"; body: ProjectUpdate; response: Project };
  updateTemplate: { method: "PUT"; path: "/templates/{name}"; body: TemplateUpsert; response: TemplateSummary };
  updateWorkItem: { method: "PATCH"; path: "/work-items/{id}"; body: WorkItemUpdate; response: WorkItem };
  validateWorkflow: { method: "POST"; path: "/workflows/validate"; body: WorkflowValidateRequest; response: WorkflowValidation };
}

export type OperationId = keyof Operations;
