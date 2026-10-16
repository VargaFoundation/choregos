// SPDX-License-Identifier: Apache-2.0
/* eslint-disable */
/**
 * Généré par tools/gen_contracts.py — NE PAS MODIFIER À LA MAIN.
 * Source : packages/contracts/openapi.yaml
 * Régénérer : make contracts
 */


import type * as S from "./schemas";

/** #rrggbb only: no var(), no name, no alpha, so it is safe to emit as CSS and measurable for contrast */
export type ThemeColour = string;

/** Sparse overrides of the console's colour roles (the names are the `--choregos-*` custom properties of `theme.css` without the prefix). A role left out keeps the console default. */
export type ThemeTokens = {
  accent?: ThemeColour;
  "accent-hover"?: ThemeColour;
  "accent-ink"?: ThemeColour;
  "accent-line"?: ThemeColour;
  "accent-soft"?: ThemeColour;
  "accent-strong"?: ThemeColour;
  "agent-ink"?: ThemeColour;
  "agent-line"?: ThemeColour;
  "agent-soft"?: ThemeColour;
  "agent-solid"?: ThemeColour;
  danger?: ThemeColour;
  "failed-ink"?: ThemeColour;
  "failed-line"?: ThemeColour;
  "failed-soft"?: ThemeColour;
  "failed-solid"?: ThemeColour;
  focus?: ThemeColour;
  ink?: ThemeColour;
  "ink-muted"?: ThemeColour;
  "ink-placeholder"?: ThemeColour;
  "ink-subtle"?: ThemeColour;
  inverse?: ThemeColour;
  "inverse-hover"?: ThemeColour;
  "inverse-ink"?: ThemeColour;
  line?: ThemeColour;
  "line-strong"?: ThemeColour;
  "neutral-ink"?: ThemeColour;
  "neutral-line"?: ThemeColour;
  "neutral-soft"?: ThemeColour;
  "neutral-solid"?: ThemeColour;
  ok?: ThemeColour;
  raised?: ThemeColour;
  "retrying-ink"?: ThemeColour;
  "retrying-line"?: ThemeColour;
  "retrying-soft"?: ThemeColour;
  "retrying-solid"?: ThemeColour;
  "running-ink"?: ThemeColour;
  "running-line"?: ThemeColour;
  "running-soft"?: ThemeColour;
  "running-solid"?: ThemeColour;
  sidebar?: ThemeColour;
  "succeeded-ink"?: ThemeColour;
  "succeeded-line"?: ThemeColour;
  "succeeded-soft"?: ThemeColour;
  "succeeded-solid"?: ThemeColour;
  surface?: ThemeColour;
  "surface-muted"?: ThemeColour;
  "surface-sunken"?: ThemeColour;
  "waiting-ink"?: ThemeColour;
  "waiting-line"?: ThemeColour;
  "waiting-soft"?: ThemeColour;
  "waiting-solid"?: ThemeColour;
  warn?: ThemeColour;
};

/** A file served by the API under /api/v1 (same origin: the console's CSP allows font-src and img-src 'self' only) */
export type ThemeAsset = string;

export type ThemeFace = {
  src: ThemeAsset;
  weight: number;
  style: "normal" | "italic";
};

export type ThemeFont = {
  family: string;
  /** The weight titles are set in */
  weight: number;
  faces: Array<ThemeFace>;
};

export type Theme = {
  /** `default` in the community edition */
  id: string;
  /** The wordmark text and the document-title suffix */
  product_name: string;
  /** The mode used when the person has no preference */
  default_mode: "dark" | "light";
  tokens: {
    dark: ThemeTokens;
    light: ThemeTokens;
  };
  radius?: {
    /** px; absent = the console default */
    control?: number;
    /** px; absent = the console default */
    surface?: number;
  };
  /** The mono font stays Geist Mono */
  fonts?: {
    title?: ThemeFont;
    sans?: ThemeFont;
  };
  logo?: ThemeAsset;
  logo_dark?: ThemeAsset;
  favicon?: ThemeAsset;
  footer?: {
    text: string;
  };
};

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
    /** Machine code of a workflow or policy issue */
    code?: string;
    /** @deprecated Former spelling of a code renamed to English, emitted for one release (ADR 0046) */
    deprecated_code?: string;
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
  /** An active workflow of the project has a `via: release_train` transition: only then does the project have trains. */
  has_release_train?: boolean;
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
  /** the secrets, field by field, as references (`env:NAME`, or `store:...` for a sealed value) — never their value (ADR 0034, ADR 0058) */
  secret_refs?: {
    [key: string]: string;
  };
  /** one state per configured secret field: where it comes from and when it was last set */
  secrets?: Array<ConnectorSecretState>;
  status: "unknown" | "ok" | "degraded" | "error";
  last_check_at?: string | null;
  last_error?: string | null;
};

/** What the console may know of one secret field. Never its value (ADR 0058). */
export type ConnectorSecretState = {
  field: string;
  source: "store" | "reference";
  /** only for source=reference: the reference itself (`env:NAME`) */
  reference?: string | null;
  /** only for source=store: +1 at every set; null when the sealed value is gone */
  version?: number | null;
  rotated_at?: string | null;
  rotated_by?: string | null;
};

export type SecretValue = {
  value: string;
};

export type SecretsRewrap = {
  rewrapped: number;
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
  /** secret values typed in the console, field -> value: sealed, replaced by a derived `store:` reference, never returned. A stored secret the body does not name is kept (ADR 0058). */
  secret_values?: {
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
  /** the identity-provider group whose members approve the actions this operation proposes; none: the project's owners */
  approver_group?: string | null;
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
  secrets?: Array<ConnectorSecretState>;
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
  /** secret values typed in the console, field -> value: sealed, never returned (ADR 0058) */
  secret_values?: {
    [key: string]: string;
  };
};

/** Absent fields are unchanged; `secret_refs` and `secret_values` merge into the connector's. */
export type OrgConnectorUpdate = {
  config?: {
    [key: string]: unknown;
  } | null;
  secret_refs?: {
    [key: string]: string;
  } | null;
  secret_values?: {
    [key: string]: string;
  } | null;
};

export type OperationPatch = {
  policy?: "allowed" | "approval" | "forbidden" | null;
  groups?: Array<string> | null;
  price_usd?: number | null;
  /** an identity-provider group; null clears it */
  approver_group?: string | null;
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
    /** an identity-provider group the approver must also belong to (ADR 0052) */
    group?: string | null;
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
  actor_type: "agent" | "human" | "system" | "job" | "release_train";
  who: string;
  outputs?: Array<string>;
  gates?: Array<{
    name?: string;
    summary?: string;
  }>;
  on_fail?: string | null;
  on_reject?: string | null;
  timeout_hours?: number | null;
  /** The governed action the transition proposes */
  action?: string | null;
  /** The task a person does */
  task?: string | null;
  sentence: string;
  /** The registry agent the actor names, `slug` or `slug@N` (ADR 0033); null for an implicit playbook actor and for any actor that is not an agent. */
  agent?: string | null;
  /** The role of an agent or job actor. */
  role?: string | null;
  /** The model or profile of an agent actor, as written (`profile:standard`). */
  model?: string | null;
  /** The group of a human actor. */
  group?: string | null;
  /** The SLA of a human actor. */
  sla_hours?: number | null;
  /** The argv of a job actor. */
  command?: Array<string> | null;
  /** The image name of a job actor. */
  image?: string | null;
  /** The documents the step reads (`inputs:` of the transition). */
  inputs?: Array<string>;
  /** The declared file type of an output (ADR 0049); an output not listed is inline, markdown or JSON. */
  output_types?: {
    [key: string]: string;
  };
  /** True for an agent or job step whose transition carries a gate that reads the diff: it changes the work item's branch. */
  writes_branch?: boolean;
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
  monthly_usd?: number | null;
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
  /** the knowledge bases the agent reads (ADR 0051); each must be served to the run's project */
  knowledge?: Array<{
    slug: string;
  }>;
  /** The agent's eval suite (S32-03): every case of the published version and of the new one must pass before the new one is published */
  evals?: Array<AgentEvalCase>;
  /** whose memory a run reads and writes (S32-02): the project's shared memory, the agent's own within the project, or none */
  memory?: "none" | "project" | "agent";
};

/** A deterministic case (ADR 0020, level 1) — the instructions rendered on its sample input contain what they must, and nothing they must not. */
export type AgentEvalCase = {
  name: string;
  input?: AgentSampleInput;
  must_contain?: Array<string>;
  must_not_contain?: Array<string>;
};

/** A sample work item an agent is tried or evaluated on. */
export type AgentSampleInput = {
  title?: string;
  body?: string;
};

export type AgentEvalResult = {
  name: string;
  passed: boolean;
  missing?: Array<string>;
  forbidden?: Array<string>;
  /** the instructions do not render on this input */
  error?: string | null;
};

/** Try a draft version on a sample input, in one project, under a capped budget (S32-03). */
export type AgentTrialCreate = {
  /** the project's slug: its model profiles, backend and tools apply */
  project: string;
  spec: AgentSpec;
  input: AgentSampleInput;
  /** capped at 0.50 $, at the version's budget per run and at the project's budget per work item */
  budget_usd?: number | null;
  /** the agent the draft would become a version of: its published suite is run too */
  agent?: string | null;
};

export type AgentTrial = {
  /** read it at `GET /runs/{id}` */
  run_id: string;
  work_item_id: string;
  project: string;
  budget_usd: number;
  status: string;
  evals?: Array<AgentEvalResult>;
};

export type AgentCreate = {
  /** `new` is reserved: the console's `/agents/new` page would shadow it (422) */
  slug: string;
  kind?: "internal" | "external";
  display_name: string;
  description?: string | null;
  spec?: AgentSpec;
  /** a draft is neither pinned nor run until it is activated */
  lifecycle?: "draft" | "active";
  /** who owns the agent (ADR 0061); `platform` is never asked for, a common agent comes from the catalogue */
  scope?: "org" | "project" | "personal";
  /** the project's slug: required for, and only for, a `project` agent */
  project?: string | null;
  /** what saving a revision does (ADR 0071); absent, the defaults: it goes to production */
  release_policy?: AgentReleasePolicy | null;
};

export type AgentFork = {
  /** slugs are unique per organisation: a fork takes a new one */
  slug: string;
  display_name?: string | null;
  scope: "org" | "project" | "personal";
  /** required for, and only for, a `project` fork */
  project?: string | null;
  /** the source version to copy; absent, its latest */
  version?: number | null;
};

export type AgentScopeChange = {
  scope: "org" | "project" | "personal";
  /** required for, and only for, a move to a `project` */
  project?: string | null;
};

/** What a fork was copied from, and whether the source moved on since (ADR 0061). */
export type AgentUpstream = {
  slug: string;
  /** the version the fork copied */
  version: number;
  /** absent when the source is no longer visible to the caller */
  scope?: "platform" | "org" | "project" | "personal" | null;
  latest_version?: number | null;
  /** the source published a version newer than the one copied */
  behind?: boolean;
  /** false when the source is deleted or not visible to the caller */
  available?: boolean;
};

/** What the caller may do with an agent, decided by the server (ADR 0061). */
export type AgentPermissions = {
  /** publish a version, edit it, move it through its life */
  publish?: boolean;
  /** suspend, revoke, change its owner, attach clients */
  manage?: boolean;
  /** pin it on a project the caller owns */
  pin?: boolean;
  /** send a revision to testing or production, roll back (ADR 0071) */
  release?: boolean;
  fork_scopes?: Array<"org" | "project" | "personal">;
};

export type AgentDraftRequest = {
  display_name: string;
  /** what the agent is for; data for the drafting model */
  description: string;
};

/** A proposal that is never saved; the builder is pre-filled with it and a person creates the agent. */
export type AgentDraft = {
  display_name: string;
  slug: string;
  description: string;
  spec: AgentSpec;
  sample: AgentSampleInput;
  /** what the platform changed or dropped in the model's answer */
  notes?: Array<string>;
  /** the model that drafted it */
  model: string;
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
  /** the scope of the agent of this name in the organisation, if any (ADR 0061) */
  scope?: "platform" | "org" | "project" | "personal" | null;
  update_available?: boolean;
  /** false for a client the door does not accept yet (OAuth, registered client, https) */
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
  /** the channels this revision sits on now (ADR 0071) */
  channels?: Array<"stable" | "candidate">;
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
  /** where the agent is in its life (S32-04); apart from the operational status */
  lifecycle?: "draft" | "active" | "deprecated" | "retired";
  retired_at?: string | null;
  last_run_at?: string | null;
  /** no run for 60 days (since its creation if it never ran) */
  unused?: boolean;
  /** who owns it (ADR 0061) */
  scope?: "platform" | "org" | "project" | "personal";
  /** the slug of its project, for a `project` agent */
  project?: string | null;
  /** set on a fork */
  forked_from?: AgentUpstream | null;
  can?: AgentPermissions;
  /** the server's sentence for what the caller may not do: shown as is, never guessed from roles */
  cannot_reason?: string | null;
  /** the revision in production (ADR 0071) */
  stable_version?: number | null;
  /** the revision in testing */
  candidate_version?: number | null;
  /** the share of work items, of projects that follow production, served the candidate */
  candidate_percent?: number;
  candidate_since?: string | null;
  release_policy?: AgentReleasePolicy;
};

/** What a revision must satisfy before it reaches production; a rollback skips them. */
export type AgentStableGates = {
  /** the revision must be the current candidate */
  require_candidate?: boolean;
  /** finished runs of the candidate since it became the candidate */
  min_candidate_runs?: number;
  min_success_rate?: number | null;
  min_soak_hours?: number | null;
  /** how recent the person's sign-in must be */
  step_up_minutes?: number;
};

/** What saving a revision does, and the gates of production (ADR 0071). `on_publish: stable` cannot be combined with `require_candidate` or a candidate threshold (422): a revision published straight to production is never a candidate. */
export type AgentReleasePolicy = {
  /** `stable`: a saved revision goes to production (the behaviour before channels); `candidate`: to testing; `none`: nowhere */
  on_publish?: "stable" | "candidate" | "none";
  stable?: AgentStableGates;
};

export type AgentReleaseCreate = {
  channel: "candidate" | "stable";
  /** `null` with `candidate` withdraws the candidate; `null` with `stable` releases the current candidate */
  version?: number | null;
  /** candidate only: the share of work items served the candidate; absent keeps the current share (0 for a new candidate) */
  percent?: number | null;
  reason?: string | null;
};

export type AgentReleaseCheck = {
  /** eval_suite, newer_than_production, is_candidate, candidate_runs, candidate_success_rate, soak, previously_in_production, human_session, fresh_authentication */
  name: string;
  required?: boolean;
  /** null: not applicable */
  passed?: boolean | null;
  detail?: string | null;
};

export type AgentRelease = {
  id: string;
  agent: string;
  channel: "candidate" | "stable";
  kind: "publish" | "promote" | "rollback" | "withdraw" | "baseline";
  version?: number | null;
  previous_version?: number | null;
  percent?: number | null;
  status: "done";
  requested_by?: string | null;
  requested_at: string;
  done_at?: string | null;
  reason?: string | null;
  /** the checks as they were evaluated (a snapshot) */
  checks?: Array<AgentReleaseCheck>;
};

export type AgentChannelStats = {
  version: number;
  /** finished runs */
  runs?: number;
  succeeded?: number;
  failed?: number;
  success_rate?: number | null;
  since?: string | null;
};

export type AgentReleaseReadiness = {
  version: number;
  channel: "candidate" | "stable";
  kind: "publish" | "promote" | "rollback" | "withdraw" | "baseline";
  /** every required check passes */
  ready: boolean;
  checks?: Array<AgentReleaseCheck>;
  candidate?: AgentChannelStats | null;
  production?: AgentChannelStats | null;
};

export type AgentExposure = {
  stable_version?: number | null;
  candidate_version?: number | null;
  candidate_percent?: number;
  projects?: Array<{
    project: string;
    source: "pin" | "pin_channel" | "project_channel";
    channel?: "stable" | "candidate" | null;
    version: number;
  }>;
};

export type ProjectAgentChannel = {
  /** which channel of the agents the project runs when it pins nothing */
  channel: "stable" | "candidate";
};

export type AgentLifecycleChange = {
  to: "active" | "deprecated" | "retired";
  /** retire although runs of the agent are in flight */
  force?: boolean;
  reason?: string | null;
};

export type AgentLifecycleResult = {
  agent: Agent;
  revoked_credentials?: number;
  unpinned_projects?: Array<string>;
  running_work_items?: Array<string>;
  forced?: boolean;
};

export type AgentOwnerChange = {
  email: string;
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
  /** production's daily budget */
  daily_budget_usd?: number | null;
  by_version?: Array<AgentVersionMetrics>;
};

export type AgentVersionMetrics = {
  version: number;
  runs?: number;
  succeeded?: number;
  failed?: number;
  success_rate?: number | null;
  cost_usd?: number;
};

/** An agent's line in the registry over a period. */
export type AgentMetricsSummary = {
  agent: string;
  days: number;
  runs: number;
  success_rate?: number | null;
  cost_usd?: number;
};

export type AgentResolution = {
  /** The reference as asked (`slug` or `slug@N`). */
  ref: string;
  slug: string;
  /** `resolved`: a run would load `version`. `not_installed`: the organisation has no such agent (`in_catalogue` says whether publishing the workflow installs it, ADR 0040). `unavailable`: it exists but a run would not start (`reason`: draft, retired, expired, disabled, no such version). */
  status: "resolved" | "not_installed" | "unavailable";
  version?: number | null;
  /** True when the version comes from the project's pin (fixed or by channel) rather than from the reference or from the agent's channels. */
  pinned: boolean;
  /** why this revision (ADR 0071); `latest` only for an agent that has no production pointer */
  source?: "explicit" | "pin" | "pin_channel" | "project_channel" | "canary" | "stable" | "latest" | null;
  channel?: "stable" | "candidate" | null;
  /** the revision in testing, if any: the console says `v4, 10 % of work items run v5` */
  candidate_version?: number | null;
  candidate_percent?: number | null;
  display_name?: string | null;
  lifecycle?: string | null;
  in_catalogue: boolean;
  reason?: string | null;
};

/** A workflow actor that runs as an agent without naming one of the registry. */
export type ImplicitAgent = {
  workflow: string;
  actor: string;
  role?: string | null;
  model?: string | null;
  backend?: string | null;
  max_turns?: number | null;
  max_minutes?: number | null;
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
  /** the token's last call to the MCP door: the console shows it as connected */
  last_used_at?: string | null;
  /** the client of that last call (`User-Agent`, truncated) */
  last_client?: string | null;
  /** the slug of the agent the client is attached to */
  agent?: string | null;
};

/** What a project changes in a version — tightening only. */
export type AgentOverrides = {
  limits?: AgentLimits;
  budget?: AgentBudget;
  tools?: Array<string> | null;
};

/** Exactly one of `version` (a fixed revision) and `channel` (follow production or testing); the server answers 422 otherwise. */
export type ProjectAgentPut = {
  version?: number | null;
  channel?: "stable" | "candidate" | null;
  overrides?: AgentOverrides;
};

export type ProjectAgent = {
  agent: string;
  /** the revision served now (the channel's, for a channel pin) */
  version: number;
  /** set when the pin follows a channel */
  channel?: "stable" | "candidate" | null;
  overrides: AgentOverrides;
  effective: AgentSpec;
  /** said when the agent is deprecated */
  warning?: string | null;
};

export type SkillFiles = {
  /** relative path → text */
  files: {
    [key: string]: string;
  };
};

/** Where a version imported from git comes from (ADR 0050). */
export type SkillSource = {
  repo: string;
  /** the full commit SHA the version is pinned to */
  sha: string;
  /** the skill's folder in the repository; empty is the root */
  path: string;
  /** an SPDX identifier, LicenseRef-proprietary, LicenseRef-unknown, or null when none was found */
  licence?: string | null;
};

export type SkillVersion = {
  version: number;
  digest: string;
  created_by?: string | null;
  created_at?: string | null;
  files?: {
    [key: string]: string;
  } | null;
  source?: SkillSource | null;
  /** the gate's tier; null before the gate existed */
  risk?: "low" | "medium" | "high" | null;
  /** a high-risk version waits for an administrator who is not its importer; until then no agent pins it and no run receives it */
  review_status?: "approved" | "pending_review";
  findings?: Array<SkillFinding>;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
};

/** What the gate found in a version (ADR 0050). */
export type SkillFinding = {
  /** script, executable, outbound_url, credential, mcp_reference, prompt_injection, hidden_unicode, obfuscated, pipe_to_shell, licence_missing, licence_not_open */
  code: string;
  severity: "info" | "warning" | "critical";
  detail: string;
  file?: string | null;
  line?: number | null;
};

export type SkillSources = {
  sources: Array<string>;
  /** true when the organisation has not written its own list */
  default?: boolean;
};

export type SkillGitPreviewRequest = {
  /** https://github.com/<owner>/<repo> or https://gitlab.com/<group>/…/<project> */
  repo_url: string;
  /** a folder holding one skill */
  path?: string;
  /** a branch */
  ref: string;
};

export type SkillGitImport = {
  repo_url: string;
  path?: string;
  /** a full commit SHA; a branch or a tag is refused */
  sha: string;
  skills: Array<string>;
};

export type SkillCandidate = {
  path: string;
  name?: string | null;
  description?: string | null;
  files: number;
  bytes: number;
  licence?: string | null;
  /** why it cannot be imported */
  problem?: string | null;
  /** the latest version of the library's skill of that name */
  existing_version?: number | null;
  /** the gate's tier; null before the gate existed */
  risk?: "low" | "medium" | "high" | null;
  findings?: Array<SkillFinding>;
};

export type SkillGitPreview = {
  repo: string;
  ref: string;
  sha: string;
  path: string;
  /** whether the repository is in the organisation's allowlist */
  allowed: boolean;
  skills: Array<SkillCandidate>;
};

export type SkillUpdate = {
  slug: string;
  version: number;
  source: SkillSource;
  ref: string;
  sha: string;
  up_to_date: boolean;
  digest?: string | null;
  added?: Array<string>;
  removed?: Array<string>;
  modified?: Array<string>;
  problem?: string | null;
};

export type Skill = {
  slug: string;
  description?: string | null;
  status: string;
  latest_version: number;
  used_by?: Array<string>;
  versions?: Array<SkillVersion> | null;
  /** the gate's tier of the latest version; null before the gate existed */
  risk?: "low" | "medium" | "high" | null;
  review_status?: "approved" | "pending_review";
};

export type KnowledgeBaseCreate = {
  slug: string;
  name: string;
  description?: string | null;
  /** `org`: every project; `projects`: the listed ones; `groups`: humans of the listed IdP groups (never a run) */
  scope?: "org" | "projects" | "groups";
  projects?: Array<string>;
  groups?: Array<string>;
  /** `files`: the converted corpus mounted read-only in the workspace; `index`: hybrid search */
  serving_mode?: "files" | "index";
};

export type ProjectKnowledgeBaseCreate = {
  slug: string;
  name: string;
  description?: string | null;
  serving_mode?: "files" | "index";
};

export type KnowledgeBasePatch = {
  name?: string | null;
  description?: string | null;
  scope?: "org" | "projects" | "groups" | null;
  projects?: Array<string> | null;
  groups?: Array<string> | null;
  serving_mode?: "files" | "index" | null;
  status?: "active" | "archived" | null;
};

export type KnowledgeDocument = {
  id: string;
  filename: string;
  /** the format's own title, else the first heading, else the file name */
  title: string;
  mime: string;
  size_bytes: number;
  /** sha256 of the uploaded bytes */
  digest: string;
  /** `upload` */
  source: string;
  status: "pending" | "converting" | "ready" | "failed";
  error?: string | null;
  created_by?: string | null;
  created_at?: string | null;
  converted_at?: string | null;
  /** returned by the reading of one document */
  markdown?: string | null;
  /** the source it was synced from; none for an upload */
  source_id?: string | null;
  /** where to open the original */
  source_url?: string | null;
};

export type KnowledgeBase = {
  slug: string;
  name: string;
  description?: string | null;
  scope: "org" | "projects" | "groups";
  projects?: Array<string>;
  groups?: Array<string>;
  serving_mode: "files" | "index";
  status: string;
  document_count?: number;
  documents_by_status?: {
    [key: string]: number;
  };
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  documents?: Array<KnowledgeDocument> | null;
  /** `index` mode: chunks, documents indexed, and the search mode (`hybrid`, `full-text` without pgvector or embedder, `in-process` on SQLite) */
  index?: {
    chunks?: number;
    documents?: number;
    mode?: string;
  } | null;
  eval_set?: Array<KnowledgeEvalCase>;
  /** returned by the reading of one base */
  sources?: Array<KnowledgeSource> | null;
  /** the project this base was created from (S39-14): its owners curate it, never widen it; null for an organisation's base */
  owner_project?: string | null;
  /** the last distillation: `status` (running, pending_review, failed), `skill`, `version`, `principles`, `dropped` (principles citing nothing of the base), `error`, `at` */
  distillation?: {
    [key: string]: unknown;
  } | null;
  /** the last run of the eval set */
  eval_result?: {
    k?: number;
    /** expected documents found in the top k / expected documents */
    recall?: number | null;
    mode?: string;
    ran_at?: string;
    cases?: Array<{
      question?: string;
      expected?: Array<string>;
      found?: Array<string>;
      returned?: Array<string>;
    }>;
  } | null;
};

export type KnowledgeSourceCreate = {
  kind: "sharepoint" | "confluence" | "web" | "git";
  name: string;
  /** `sharepoint`, `confluence`: the organisation connector of capability `documents` */
  connector?: string | null;
  /** `sharepoint`: `site_id` or `drive_id`, optional `folder`; `confluence`: `space_key`, optional `page_id` (that page and its descendants), `include_root` (default true), `include_attachments` (default false), `labels` (≤ 20); `web`: `url` (https), `max_depth` (≤ 5), `max_pages` (≤ 500); `git`: `url` (https), `ref`, `paths` (globs, default `*.md`) */
  config?: {
    [key: string]: unknown;
  };
  interval_minutes?: number;
  /** `interval`: every `interval_minutes`; `cron`: `refresh_cron` in `refresh_timezone`; `manual`: only on demand (S39-19) */
  refresh_mode?: "interval" | "cron" | "manual";
  /** five fields (minute hour day month weekday); at least 5 minutes between two fires */
  refresh_cron?: string | null;
  /** IANA name */
  refresh_timezone?: string;
};

export type KnowledgeSourcePatch = {
  name?: string | null;
  config?: {
    [key: string]: unknown;
  } | null;
  interval_minutes?: number | null;
  refresh_mode?: "interval" | "cron" | "manual" | null;
  refresh_cron?: string | null;
  refresh_timezone?: string | null;
};

export type KnowledgeSource = {
  id: string;
  kind: "sharepoint" | "confluence" | "web" | "git";
  name: string;
  connector?: string | null;
  config?: {
    [key: string]: unknown;
  };
  interval_minutes: number;
  refresh_mode?: "interval" | "cron" | "manual";
  refresh_cron?: string | null;
  refresh_timezone?: string;
  /** an estimate (the worker may be down); null in manual mode or when due */
  next_sync_at?: string | null;
  sync_requested_at?: string | null;
  status: "idle" | "syncing" | "failed";
  last_synced_at?: string | null;
  last_error?: string | null;
  /** what the last sync did */
  last_summary?: {
    added?: number;
    updated?: number;
    deleted?: number;
    unchanged?: number;
    skipped?: number;
    skipped_reasons?: Array<string>;
  } | null;
  document_count?: number;
};

export type KnowledgeEvalCase = {
  question: string;
  /** file names or titles */
  expected: Array<string>;
};

export type KnowledgeEvalSet = {
  cases?: Array<KnowledgeEvalCase>;
};

export type KnowledgeSearchRequest = {
  query: string;
  k?: number;
  /** internal search: one base of the run; absent, all of them */
  knowledge_base?: string | null;
};

export type KnowledgeCitation = {
  knowledge_base: string;
  document_id: string;
  /** the document's title */
  document: string;
  filename: string;
  heading_path: Array<string>;
  excerpt: string;
  /** reciprocal rank fusion score */
  score: number;
};

export type KnowledgeSearchResult = {
  mode: "hybrid" | "full-text" | "in-process";
  hits: Array<KnowledgeCitation>;
};

export type WorkflowIssue = {
  code: string;
  message: string;
  path?: string | null;
  line?: number | null;
  column?: number | null;
  /** @deprecated Former spelling of a code renamed to English (`gate.sans_matiere` is now `gate.nothing_to_check`, `workflow.effet_implicite` is now `workflow.implicit_effect`), emitted for one release (ADR 0046) */
  deprecated_code?: string | null;
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
  /** The artifact flow (S39-13), one entry per document the workflow produces or reads. */
  artifacts?: Array<ArtifactFlow>;
};

export type ArtifactFlow = {
  name: string;
  /** The declared file type of the first producer's output; null for an inline value. */
  type?: string | null;
  /** The ids of the transitions that output it. */
  produced_by: Array<string>;
  /** The ids of the transitions that list it in `inputs`. */
  read_by: Array<string>;
  /** `step`: some transition outputs it. `feedback`: `human_feedback`, written by a person's rejection (`produced_by` lists the human transitions with `on_reject`). `field`: a property of `metadata.inputs`. `missing`: read, but nothing produces it. */
  origin: "step" | "feedback" | "field" | "missing";
  /** True when the platform gives it to every agent stage. */
  context?: boolean;
  /** True when the pull request body carries it. */
  in_pull_request?: boolean;
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
  /** what the organisation's ceiling brings down in this policy; the policy that runs is the clamped one */
  clamps?: Array<string>;
};

/** Caps a project policy may only tighten (S27-05). A per-role table's `default` caps every role it does not name; a cap left out leaves the project free. */
export type PolicyCeiling = {
  budgets?: {
    ticket_usd?: {
      [key: string]: number;
    };
    stage_usd?: {
      [key: string]: {
        [key: string]: number;
      };
    };
    max_turns?: {
      [key: string]: number;
    };
    max_minutes?: {
      [key: string]: number;
    };
    tool_calls_per_run?: number | null;
    daily_project_usd?: number | null;
  };
  attempts?: {
    default_max?: number | null;
    by_role?: {
      [key: string]: number;
    };
    dod_iterations?: number | null;
  };
  scope?: {
    auto_grant_max_files?: number | null;
    max_diff_files?: number | null;
    max_diff_lines?: number | null;
  };
  findings?: {
    max_per_run?: number | null;
  };
};

export type PolicyCeilingState = {
  ceiling?: PolicyCeiling | null;
  updated_by?: string | null;
  updated_at?: string | null;
};

export type PolicyCeilingPut = {
  ceiling: PolicyCeiling | null;
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
    /** The context reduction the cell's runs were given (ADR 0055). */
    context?: "none" | "rtk" | "headroom" | "rtk+headroom" | null;
    /** Cases the gateway's ledger has a measure for. */
    metered?: number | null;
    /** Input tokens from the ledger, cache reads included. */
    median_tokens_in?: number | null;
    /** Cache-read input tokens from the ledger. */
    median_tokens_cached?: number | null;
    median_tokens_out?: number | null;
    median_turns?: number | null;
  }>;
};

export type Org = {
  slug: string;
  name: string;
  role?: Role | null;
  /** the currency the console shows spend in (S29-03); an organisation setting */
  currency?: "EUR" | "USD";
};

export type OrgCreate = {
  slug: string;
  name: string;
};

/** What changes in an organisation; the slug is in every URL and every MCP address. */
export type OrgUpdate = {
  slug?: string | null;
  name?: string | null;
  /** the currency spend is shown in; org_admin */
  currency?: "EUR" | "USD" | null;
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
  /** The most this work item may spend, in USD. Only tightens the workflow's `budget.ticket_usd` and the policy's ticket budget; wider is 422 (ADR 0047). */
  budget_usd?: number | null;
};

export type WorkItemUpdate = {
  /** The fields that change; `null` removes one. */
  fields: {
    [key: string]: unknown;
  };
};

export type Integrations = {
  /** The MCP door's URL; a project's adds `/projects/{org}:{slug}` to it. */
  mcp_url: string;
  protocol_versions: Array<string>;
  oauth: {
    /** False as long as the door accepts only scoped tokens. */
    enabled: boolean;
    authorization_server?: string | null;
    /** The clients the IdP registered for the door, by client of the Integrations page (`claude-code`, `claude-ai`…): the page derives the exact command from them. */
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

/** `*`: the REST API and the CLI. `mcp:read`, `mcp:write`: the MCP door only (ADR 0030) — such a token is refused by the REST API, and the door refuses `*`. */
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
  /** The budget given at launch or by the workflow (ADR 0047); the policy's applies otherwise. */
  budget_usd?: number | null;
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

/** An output of a step and its receipts, in order (S25-04). */
export type HandOff = {
  /** spec_markdown, plan_markdown, or an output a transition declares */
  output: string;
  /** The digest of what the output is worth today; null when it is gone. */
  current_digest?: string | null;
  events: Array<HandOffEvent>;
};

/** A run produced or read an output, under this digest; a receipt never changes. */
export type HandOffEvent = {
  kind: "produced" | "read";
  /** sha256:<hex> of the value's canonical JSON */
  digest: string;
  run_id: string;
  stage?: string | null;
  attempt?: number | null;
  at: string;
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
  /** The evidence of an agent run (tests, lint, coverage, business facts…): what the platform measured, which `measured` lists, and what the agent declared, which it does not (ADR 0045). */
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
  /** `rerun_stage`: USD granted on top of the work item's budget, recorded in the audit log. Required when the budget is spent — otherwise 409 (ADR 0047). */
  top_up_usd?: number | null;
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

/** The runner pod's log tail (last 200 lines, each redacted) and its termination. `live` is a snapshot the orchestrator refreshes at each poll while the pod runs; `archive` is written once when the run ends; `none` when nothing was captured. */
export type RunLogs = {
  source: "live" | "archive" | "none";
  state: string;
  exit_code?: number | null;
  /** The runtime's word: OOMKilled, Error, ErrImagePull, ImagePullBackOff… */
  reason?: string | null;
  message?: string;
  lines?: Array<string>;
  truncated?: boolean;
  /** Why the logs could not be read */
  error?: string | null;
  captured_at?: string | null;
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

export type BudgetAlert = {
  id: string;
  scope: "project" | "agent";
  /** the project's or the agent's slug */
  subject: string;
  /** day */
  period: string;
  /** the day */
  period_key: string;
  /** the share of the budget crossed, in percent */
  threshold: number;
  spent_usd: number;
  budget_usd: number;
  status: "open" | "acknowledged";
  created_at?: string | null;
  acknowledged_by?: string | null;
  acknowledged_at?: string | null;
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
  kind: "tekton" | "k8s_job" | "local_docker" | "aca" | "agent_sandbox";
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

export type Artifact = S.Artifact;

/** A declared input of a run, as the runner mounts it in `.choregos/inputs/` (ADR 0049). */
export type RunInputFile = {
  name: string;
  version: number;
  /** The name with its type's extension: `deck.pptx`. */
  filename: string;
  type: string;
  media_type: string;
  /** sha256 of the bytes */
  digest: string;
  size: number;
};

export type ContextPack = S.ContextPack;

export type Finding = S.Finding;

export type InboundEvent = S.InboundEvent;

export type ChoregosEvent = S.Event;

export type HumanDecision = S.HumanDecision;

export interface Operations {
  abortRelease: { method: "POST"; path: "/releases/{id}/abort"; body: {
  reason?: string;
}; response: void };
  acknowledgeBudgetAlert: { method: "POST"; path: "/orgs/{org}/budget-alerts/{alert_id}/acknowledge"; body: never; response: BudgetAlert };
  addMember: { method: "POST"; path: "/orgs/{org}/members"; body: MembershipUpsert; response: Membership };
  alertmanagerWebhook: { method: "POST"; path: "/webhooks/alertmanager"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  approveRelease: { method: "POST"; path: "/releases/{id}/approve"; body: {
  note?: string;
}; response: void };
  approveSkillVersion: { method: "POST"; path: "/orgs/{org}/skills/{slug}/versions/{version}/approve"; body: never; response: SkillVersion };
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
  changeAgentLifecycle: { method: "POST"; path: "/orgs/{org}/agents/{slug}/lifecycle"; body: AgentLifecycleChange; response: AgentLifecycleResult };
  changeAgentScope: { method: "POST"; path: "/orgs/{org}/agents/{slug}/scope"; body: AgentScopeChange; response: Agent };
  checkSkillUpdate: { method: "GET"; path: "/orgs/{org}/skills/{slug}/update"; body: never; response: SkillUpdate };
  clearConnectorSecret: { method: "DELETE"; path: "/projects/{id}/connectors/{kind}/secrets/{field}"; body: never; response: Connector };
  clearOrgConnectorSecret: { method: "DELETE"; path: "/orgs/{org}/connectors/{name}/secrets/{field}"; body: never; response: OrgConnector };
  connectCatalogueClient: { method: "POST"; path: "/orgs/{org}/agent-catalogue/{slug}/connect"; body: AgentCatalogueConnect; response: AgentCatalogueConnection };
  convertKnowledgeDocument: { method: "POST"; path: "/orgs/{org}/knowledge/{slug}/documents/{document_id}/convert"; body: never; response: KnowledgeDocument };
  createAgent: { method: "POST"; path: "/orgs/{org}/agents"; body: AgentCreate; response: Agent };
  createKnowledgeBase: { method: "POST"; path: "/orgs/{org}/knowledge"; body: KnowledgeBaseCreate; response: KnowledgeBase };
  createKnowledgeSource: { method: "POST"; path: "/orgs/{org}/knowledge/{slug}/sources"; body: KnowledgeSourceCreate; response: KnowledgeSource };
  createMyToken: { method: "POST"; path: "/me/tokens"; body: ApiTokenCreate; response: ApiTokenCreated };
  createOrg: { method: "POST"; path: "/orgs"; body: OrgCreate; response: Org };
  createOrgConnector: { method: "POST"; path: "/orgs/{org}/connectors"; body: OrgConnectorCreate; response: OrgConnector };
  createProject: { method: "POST"; path: "/orgs/{org}/projects"; body: ProjectCreate; response: Project };
  createProjectKnowledgeBase: { method: "POST"; path: "/projects/{id}/knowledge"; body: ProjectKnowledgeBaseCreate; response: KnowledgeBase };
  createSkill: { method: "POST"; path: "/orgs/{org}/skills"; body: SkillFiles; response: Skill };
  createTemplate: { method: "POST"; path: "/templates"; body: TemplateUpsert; response: TemplateSummary };
  createWorkItem: { method: "POST"; path: "/projects/{id}/work-items"; body: WorkItemCreate; response: WorkItem };
  deactivateWorkflow: { method: "POST"; path: "/projects/{id}/workflows/{name}/deactivate"; body: never; response: void };
  decideAction: { method: "POST"; path: "/projects/{id}/actions/{action_id}/decision"; body: ActionDecision; response: Action };
  decidePendingMemory: { method: "POST"; path: "/projects/{id}/memory/pending"; body: MemoryDecision; response: void };
  deleteKnowledgeDocument: { method: "DELETE"; path: "/orgs/{org}/knowledge/{slug}/documents/{document_id}"; body: never; response: void };
  deleteKnowledgeSource: { method: "DELETE"; path: "/orgs/{org}/knowledge/{slug}/sources/{source_id}"; body: never; response: void };
  deleteOrgConnector: { method: "DELETE"; path: "/orgs/{org}/connectors/{name}"; body: never; response: void };
  deleteProject: { method: "DELETE"; path: "/projects/{id}"; body: never; response: void };
  departTrain: { method: "POST"; path: "/projects/{id}/trains/{env}/depart"; body: never; response: void };
  detachAgentCredential: { method: "DELETE"; path: "/orgs/{org}/agents/{slug}/credentials/{credential_id}"; body: never; response: void };
  discoverConnectorOperations: { method: "POST"; path: "/orgs/{org}/connectors/{name}/discover"; body: never; response: ConnectorDiscovery };
  distillKnowledgeBase: { method: "POST"; path: "/orgs/{org}/knowledge/{slug}/distill"; body: never; response: KnowledgeBase };
  draftAgent: { method: "POST"; path: "/orgs/{org}/agent-drafts"; body: AgentDraftRequest; response: AgentDraft };
  editWorkflow: { method: "POST"; path: "/workflows/edit"; body: WorkflowEditRequest; response: WorkflowEditResult };
  edition: { method: "GET"; path: "/edition"; body: never; response: {
  edition: "community" | "enterprise";
  features: Array<string>;
  /** the core's version */
  version: string;
  /** who publishes the running edition, as announced by its plugin; null in the community edition */
  publisher?: string | null;
  /** the version of the edition plugin; null in the community edition */
  edition_version?: string | null;
} };
  exportOrgCostsCsv: { method: "GET"; path: "/orgs/{org}/costs.csv"; body: never; response: void };
  exportProjectCostsCsv: { method: "GET"; path: "/projects/{id}/costs.csv"; body: never; response: void };
  forkAgent: { method: "POST"; path: "/orgs/{org}/agents/{slug}/fork"; body: AgentFork; response: Agent };
  freezeTrain: { method: "POST"; path: "/projects/{id}/trains/{env}/freeze"; body: {
  reason: string;
}; response: void };
  getAction: { method: "GET"; path: "/projects/{id}/actions/{action_id}"; body: never; response: Action };
  getAgent: { method: "GET"; path: "/orgs/{org}/agents/{slug}"; body: never; response: Agent };
  getAgentExposure: { method: "GET"; path: "/orgs/{org}/agents/{slug}/exposure"; body: never; response: AgentExposure };
  getAgentMetrics: { method: "GET"; path: "/orgs/{org}/agents/{slug}/metrics"; body: never; response: AgentMetrics };
  getAgentReleaseReadiness: { method: "GET"; path: "/orgs/{org}/agents/{slug}/versions/{version}/readiness"; body: never; response: AgentReleaseReadiness };
  getAgentVersion: { method: "GET"; path: "/orgs/{org}/agents/{slug}/versions/{version}"; body: never; response: AgentVersion };
  getIntegrations: { method: "GET"; path: "/integrations"; body: never; response: Integrations };
  getKnowledgeBase: { method: "GET"; path: "/orgs/{org}/knowledge/{slug}"; body: never; response: KnowledgeBase };
  getKnowledgeDocument: { method: "GET"; path: "/orgs/{org}/knowledge/{slug}/documents/{document_id}"; body: never; response: KnowledgeDocument };
  getMe: { method: "GET"; path: "/me"; body: never; response: Me };
  getMemoryAbReport: { method: "GET"; path: "/orgs/{org}/memory/ab-report"; body: never; response: MemoryAbReport };
  getModelMatrix: { method: "GET"; path: "/projects/{id}/models/matrix"; body: never; response: ModelMatrix };
  getNamedWorkflow: { method: "GET"; path: "/projects/{id}/workflows/{name}"; body: never; response: WorkflowDef };
  getOrgConnector: { method: "GET"; path: "/orgs/{org}/connectors/{name}"; body: never; response: OrgConnector };
  getOrgCosts: { method: "GET"; path: "/orgs/{org}/costs"; body: never; response: CostReport };
  getPolicy: { method: "GET"; path: "/projects/{id}/policy"; body: never; response: PolicyDef };
  getPolicyCeiling: { method: "GET"; path: "/orgs/{org}/policy-ceiling"; body: never; response: PolicyCeilingState };
  getProject: { method: "GET"; path: "/projects/{id}"; body: never; response: Project };
  getProjectAgentChannel: { method: "GET"; path: "/projects/{id}/agent-channel"; body: never; response: ProjectAgentChannel };
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
  /** Permission decisions whose kind was inferred because the agent did not state it (emitted since #27, declared by S34-03) */
  deduites?: number;
  /** Total number of denied requests */
  refusals: number;
  /** @deprecated Former name of `refusals`, still emitted for one release (ADR 0046) */
  refus?: number;
  /** What the catalogue tools called by the run cost, in euros */
  tool_cost_eur?: number;
  /** @deprecated Former name of `tool_cost_eur`, still emitted for one release (ADR 0046) */
  cout_outils_eur?: number;
  acces: Array<{
    nature: "read" | "write" | "execute" | "network" | "tool" | "autre";
    /** The file, command, address or tool asked for */
    target: string;
    /** @deprecated Former name of `target`, still emitted for one release (ADR 0046) */
    cible?: string;
    /** How many times the agent asked for it */
    requests: number;
    /** @deprecated Former name of `requests`, still emitted for one release (ADR 0046) */
    demandes?: number;
    /** How many of those requests were denied */
    refusals: number;
    /** @deprecated Former name of `refusals`, still emitted for one release (ADR 0046) */
    refus?: number;
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
  getRunInputContent: { method: "GET"; path: "/internal/runs/{id}/inputs/{name}"; body: never; response: void };
  getRunInputs: { method: "GET"; path: "/internal/runs/{id}/inputs"; body: never; response: Array<RunInputFile> };
  getRunKnowledge: { method: "GET"; path: "/internal/runs/{id}/knowledge"; body: never; response: Array<{
  slug: string;
  name?: string;
  digest: string;
  documents: Array<{
    /** relative to `.choregos/knowledge/<slug>/` */
    path: string;
    title: string;
    filename?: string;
    markdown: string;
  }>;
}> };
  getRunLogs: { method: "GET"; path: "/runs/{id}/logs"; body: never; response: RunLogs };
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
  getSkillSources: { method: "GET"; path: "/orgs/{org}/skill-sources"; body: never; response: SkillSources };
  getSkillVersion: { method: "GET"; path: "/orgs/{org}/skills/{slug}/versions/{version}"; body: never; response: SkillVersion };
  getTemplate: { method: "GET"; path: "/templates/{name}"; body: never; response: TemplateDetail };
  getTrainStatus: { method: "GET"; path: "/projects/{id}/trains/{env}"; body: never; response: TrainStatus };
  getWorkItem: { method: "GET"; path: "/work-items/{id}"; body: never; response: WorkItem };
  getWorkItemArtifact: { method: "GET"; path: "/work-items/{id}/artifacts/{ref}"; body: never; response: Artifact };
  getWorkItemArtifactContent: { method: "GET"; path: "/work-items/{id}/artifacts/{ref}/content"; body: never; response: void };
  getWorkItemHandOffs: { method: "GET"; path: "/work-items/{id}/hand-offs"; body: never; response: Array<HandOff> };
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
  importSkillsFromGit: { method: "POST"; path: "/orgs/{org}/skills/import-git"; body: SkillGitImport; response: Array<Skill> };
  installCatalogueAgent: { method: "POST"; path: "/orgs/{org}/agent-catalogue/{slug}/install"; body: AgentCatalogueInstall; response: Agent };
  jiraWebhook: { method: "POST"; path: "/webhooks/jira"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  listActions: { method: "GET"; path: "/projects/{id}/actions"; body: never; response: Array<Action> };
  listAdminSections: { method: "GET"; path: "/ui/admin-sections"; body: never; response: Array<S.UiManifest> };
  listAgentCatalogue: { method: "GET"; path: "/orgs/{org}/agent-catalogue"; body: never; response: Array<AgentCatalogueEntry> };
  listAgentCredentials: { method: "GET"; path: "/orgs/{org}/agents/{slug}/credentials"; body: never; response: Array<AgentCredential> };
  listAgentMetrics: { method: "GET"; path: "/orgs/{org}/agent-metrics"; body: never; response: Array<AgentMetricsSummary> };
  listAgentReleases: { method: "GET"; path: "/orgs/{org}/agents/{slug}/releases"; body: never; response: Array<AgentRelease> };
  listAgentRuns: { method: "GET"; path: "/orgs/{org}/agents/{slug}/runs"; body: never; response: Array<Run> };
  listAgents: { method: "GET"; path: "/orgs/{org}/agents"; body: never; response: Array<Agent> };
  listAudit: { method: "GET"; path: "/audit"; body: never; response: AuditPage };
  listBackends: { method: "GET"; path: "/platform/backends"; body: never; response: Array<AgentBackendInfo> };
  listBudgetAlerts: { method: "GET"; path: "/orgs/{org}/budget-alerts"; body: never; response: Array<BudgetAlert> };
  listConnectorTypes: { method: "GET"; path: "/connectors/types"; body: never; response: Array<ConnectorType> };
  listConnectors: { method: "GET"; path: "/projects/{id}/connectors"; body: never; response: Array<Connector> };
  listExecutors: { method: "GET"; path: "/platform/executors"; body: never; response: Array<ExecutorInfo> };
  listFindings: { method: "GET"; path: "/projects/{id}/findings"; body: never; response: FindingPage };
  listGatewayKeys: { method: "GET"; path: "/platform/gateway/keys"; body: never; response: Array<GatewayKeyInfo> };
  listImplicitAgents: { method: "GET"; path: "/projects/{id}/implicit-agents"; body: never; response: Array<ImplicitAgent> };
  listKnowledgeBases: { method: "GET"; path: "/orgs/{org}/knowledge"; body: never; response: Array<KnowledgeBase> };
  listMembers: { method: "GET"; path: "/orgs/{org}/members"; body: never; response: Array<Membership> };
  listMyTokens: { method: "GET"; path: "/me/tokens"; body: never; response: Array<ApiToken> };
  listOrgActions: { method: "GET"; path: "/orgs/{org}/actions"; body: never; response: Array<Action> };
  listOrgAgentCredentials: { method: "GET"; path: "/orgs/{org}/agent-credentials"; body: never; response: Array<AgentCredential> };
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
  listWorkItemArtifacts: { method: "GET"; path: "/work-items/{id}/artifacts"; body: never; response: Array<Artifact> };
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
  previewSkillsFromGit: { method: "POST"; path: "/orgs/{org}/skills/import-git/preview"; body: SkillGitPreviewRequest; response: SkillGitPreview };
  proposeAction: { method: "POST"; path: "/projects/{id}/actions"; body: ActionCreate; response: Action };
  provisionProject: { method: "POST"; path: "/projects/{id}/provision"; body: ProvisionRequest; response: ProvisionStatus };
  publishAgentVersion: { method: "POST"; path: "/orgs/{org}/agents/{slug}/versions"; body: AgentSpec; response: AgentVersion };
  publishSkillVersion: { method: "POST"; path: "/orgs/{org}/skills/{slug}/versions"; body: SkillFiles; response: SkillVersion };
  putBackend: { method: "PUT"; path: "/platform/backends"; body: AgentBackendUpdate; response: AgentBackendInfo };
  putConnector: { method: "PUT"; path: "/projects/{id}/connectors/{kind}"; body: ConnectorUpsert; response: Connector };
  putExecutor: { method: "PUT"; path: "/platform/executors"; body: ExecutorInfo; response: ExecutorInfo };
  putKnowledgeEvalSet: { method: "PUT"; path: "/orgs/{org}/knowledge/{slug}/eval"; body: KnowledgeEvalSet; response: KnowledgeBase };
  putNamedWorkflow: { method: "PUT"; path: "/projects/{id}/workflows/{name}"; body: WorkflowPut; response: WorkflowDef };
  putPolicy: { method: "PUT"; path: "/projects/{id}/policy"; body: PolicyPut; response: PolicyDef };
  putPolicyCeiling: { method: "PUT"; path: "/orgs/{org}/policy-ceiling"; body: PolicyCeilingPut; response: PolicyCeilingState };
  putProjectModels: { method: "PUT"; path: "/projects/{id}/models"; body: ProjectModels; response: ProjectModels };
  putRunOutput: { method: "PUT"; path: "/internal/runs/{id}/outputs/{name}"; body: void; response: Artifact };
  putSkillSources: { method: "PUT"; path: "/orgs/{org}/skill-sources"; body: SkillSources; response: SkillSources };
  putWorkflow: { method: "PUT"; path: "/projects/{id}/workflow"; body: WorkflowPut; response: WorkflowDef };
  putWorkflowRouting: { method: "PUT"; path: "/projects/{id}/workflow-routing"; body: WorkflowRouting; response: WorkflowRouting };
  readyz: { method: "GET"; path: "/readyz"; body: never; response: void };
  reimportMemory: { method: "POST"; path: "/projects/{id}/memory/reimport"; body: {
  sources?: Array<string>;
}; response: void };
  reindexKnowledgeBase: { method: "POST"; path: "/orgs/{org}/knowledge/{slug}/reindex"; body: never; response: KnowledgeBase };
  relaxProjectOperation: { method: "DELETE"; path: "/projects/{id}/operations/{connector}/{operation}"; body: never; response: void };
  releaseAgentVersion: { method: "POST"; path: "/orgs/{org}/agents/{slug}/releases"; body: AgentReleaseCreate; response: AgentRelease };
  removeMember: { method: "DELETE"; path: "/orgs/{org}/members/{user_id}"; body: never; response: void };
  resolveProjectAgents: { method: "GET"; path: "/projects/{id}/agent-resolution"; body: never; response: Array<AgentResolution> };
  restoreWorkflowVersion: { method: "POST"; path: "/projects/{id}/workflows/{name}/versions/{version}/restore"; body: never; response: WorkflowDef };
  revokeMyToken: { method: "DELETE"; path: "/me/tokens/{id}"; body: never; response: void };
  rewrapSecrets: { method: "POST"; path: "/platform/secrets/rewrap"; body: never; response: SecretsRewrap };
  runKnowledgeEval: { method: "POST"; path: "/orgs/{org}/knowledge/{slug}/eval/run"; body: never; response: KnowledgeBase };
  searchKnowledgeBase: { method: "POST"; path: "/orgs/{org}/knowledge/{slug}/search"; body: KnowledgeSearchRequest; response: KnowledgeSearchResult };
  searchMemory: { method: "GET"; path: "/projects/{id}/memory/search"; body: never; response: Array<Memory> };
  searchRunKnowledge: { method: "POST"; path: "/internal/runs/{id}/knowledge/search"; body: KnowledgeSearchRequest; response: KnowledgeSearchResult };
  setAgentReleasePolicy: { method: "PUT"; path: "/orgs/{org}/agents/{slug}/release-policy"; body: AgentReleasePolicy; response: Agent };
  setConnectorSecret: { method: "PUT"; path: "/projects/{id}/connectors/{kind}/secrets/{field}"; body: SecretValue; response: Connector };
  setOrgConnectorSecret: { method: "PUT"; path: "/orgs/{org}/connectors/{name}/secrets/{field}"; body: SecretValue; response: OrgConnector };
  setProjectAgentChannel: { method: "PUT"; path: "/projects/{id}/agent-channel"; body: ProjectAgentChannel; response: ProjectAgentChannel };
  startAgentTrial: { method: "POST"; path: "/orgs/{org}/agent-trials"; body: AgentTrialCreate; response: AgentTrial };
  suspendProject: { method: "POST"; path: "/projects/{id}/suspend"; body: never; response: Project };
  syncKnowledgeBase: { method: "POST"; path: "/orgs/{org}/knowledge/{slug}/sync"; body: never; response: KnowledgeBase };
  syncKnowledgeSource: { method: "POST"; path: "/orgs/{org}/knowledge/{slug}/sources/{source_id}/sync"; body: never; response: KnowledgeSource };
  tektonWebhook: { method: "POST"; path: "/webhooks/tekton"; body: {
  [key: string]: unknown;
}; response: WebhookAck };
  testConnector: { method: "POST"; path: "/projects/{id}/connectors/{kind}/test"; body: never; response: ConnectorTestResult };
  theme: { method: "GET"; path: "/theme"; body: never; response: Theme };
  tightenProjectOperation: { method: "PUT"; path: "/projects/{id}/operations/{connector}/{operation}"; body: ProjectOperationPut; response: ProjectOperation };
  transferAgentOwnership: { method: "POST"; path: "/orgs/{org}/agents/{slug}/owner"; body: AgentOwnerChange; response: Agent };
  unfreezeTrain: { method: "POST"; path: "/projects/{id}/trains/{env}/unfreeze"; body: never; response: void };
  unpinProjectAgent: { method: "DELETE"; path: "/projects/{id}/agents/{slug}"; body: never; response: void };
  updateAgent: { method: "PATCH"; path: "/orgs/{org}/agents/{slug}"; body: AgentPatch; response: Agent };
  updateConnectorOperation: { method: "PATCH"; path: "/orgs/{org}/connectors/{name}/operations/{operation}"; body: OperationPatch; response: ConnectorOperation };
  updateKnowledgeBase: { method: "PATCH"; path: "/orgs/{org}/knowledge/{slug}"; body: KnowledgeBasePatch; response: KnowledgeBase };
  updateKnowledgeSource: { method: "PATCH"; path: "/orgs/{org}/knowledge/{slug}/sources/{source_id}"; body: KnowledgeSourcePatch; response: KnowledgeSource };
  updateOrg: { method: "PATCH"; path: "/orgs/{org}"; body: OrgUpdate; response: Org };
  updateOrgConnector: { method: "PATCH"; path: "/orgs/{org}/connectors/{name}"; body: OrgConnectorUpdate; response: OrgConnector };
  updateProject: { method: "PATCH"; path: "/projects/{id}"; body: ProjectUpdate; response: Project };
  updateTemplate: { method: "PUT"; path: "/templates/{name}"; body: TemplateUpsert; response: TemplateSummary };
  updateWorkItem: { method: "PATCH"; path: "/work-items/{id}"; body: WorkItemUpdate; response: WorkItem };
  uploadKnowledgeDocument: { method: "POST"; path: "/orgs/{org}/knowledge/{slug}/documents"; body: void; response: KnowledgeDocument };
  validateProjectWorkflow: { method: "POST"; path: "/projects/{id}/workflows/validate"; body: WorkflowValidateRequest; response: WorkflowValidation };
  validateWorkflow: { method: "POST"; path: "/workflows/validate"; body: WorkflowValidateRequest; response: WorkflowValidation };
}

export type OperationId = keyof Operations;
