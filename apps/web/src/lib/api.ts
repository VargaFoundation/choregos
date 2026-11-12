// SPDX-License-Identifier: Apache-2.0
/**
 * The Choregos API client, typed by the generated contracts.
 *
 * Two modes: `live` (the real API, relayed by Next) and `mock` (local fixtures, so that the front
 * end moves on without the API: `NEXT_PUBLIC_API_MODE=mock`).
 */
import { type CliManifest, MANIFEST_PATH } from "./cli-download";
import { clearEmbedToken, embedHeaders, isFrameSession } from "./embed-token";
import type {
  Action,
  ActivityList,
  Agent,
  AgentStopResult,
  StopRequest,
  ConnectorDiscovery,
  ConnectorOperation,
  OperationPatch,
  McpCatalogue,
  ConnectorCatalogue,
  McpCatalogueInstall,
  ProjectOrgConnector,
  SkillCatalogue,
  OrgConnector,
  OrgConnectorCreate,
  OrgConnectorUpdate,
  InstalledPlugin,
  PluginApproval,
  PluginApprovalList,
  PluginInstall,
  PluginInstallPending,
  PluginList,
  PluginSources,
  PluginUninstallResult,
  PluginUpgrade,
  PluginUpgradeResult,
  ProjectOperation,
  ProjectRequirement,
  AgentCatalogueConnection,
  AgentCatalogueEntry,
  AgentCreate,
  AgentDraft,
  AgentDraftRequest,
  AgentFork,
  AgentScopeChange,
  RemoteAgentDiscovery,
  RemoteCredential,
  RemoteCredentialAuthorization,
  RemoteCredentialCreate,
  AgentCredential,
  AgentAccess,
  AgentLifecycleChange,
  AgentLifecycleResult,
  AgentMetrics,
  AgentEvaluation,
  AgentExposure,
  AgentRelease,
  AgentReleaseCreate,
  AgentReleaseDecision,
  AgentReleasePolicy,
  AgentReleaseReadiness,
  ProjectAgentChannel,
  AgentTrial,
  AgentTrialCreate,
  AgentMetricsSummary,
  AgentResolution,
  BuiltinAgent,
  GroupMembers,
  WorkflowActivity,
  StepCalibration,
  RoutingCalibration,
  HostedSource,
  ImplicitAgent,
  WorkflowAgent,
  AgentPatch,
  AgentSpec,
  AgentVersion,
  AgentVersionPublish,
  AgentVersionRestore,
  ProjectAgent,
  ProjectAgentPut,
  Skill,
  SkillGitImport,
  SkillGitPreview,
  SkillGitPreviewRequest,
  SkillSources,
  SkillUpdate,
  SkillVersion,
  KnowledgeBase,
  KnowledgeDistillEstimate,
  KnowledgeDistillRequest,
  KnowledgeBaseCreate,
  ProjectKnowledgeBaseCreate,
  KnowledgeBasePatch,
  KnowledgeDocument,
  KnowledgeEvalCase,
  KnowledgeGrant,
  KnowledgeGrants,
  KnowledgeSearchResult,
  KnowledgeSource,
  KnowledgeSourceCreate,
  KnowledgeSourcePatch,
  AgentBackendInfo,
  AgentBackendUpdate,
  AgentImageInfo,
  ApiToken,
  ApiTokenCreate,
  ApiTokenCreated,
  BrowserSession,
  EndedSessions,
  Artifact,
  ArtifactRef,
  AdminSection,
  AuditPage,
  ConnectorType,
  ExecutorInfo,
  PlatformIdentity,
  GatewayModel,
  ModelCatalogue,
  ModelCatalogueEntry,
  ModelCatalogueEntryCreate,
  ModelCataloguePatch,
  ModelCatalogueSettings,
  ModelCuratePreview,
  ModelSyncResult,
  UsableModel,
  GitHubAppManifestRequest,
  GitHubAppManifestStart,
  GitHubAppRegistrationStatus,
  GitHubAppStatus,
  GitHubInstallLink,
  GitHubInstallLinkRequest,
  GitHubRepositoryPage,
  HandOff,
  HumanRequest,
  AssigneeChange,
  Membership,
  MembershipUpsert,
  TrackerIdentity,
  Org,
  OrgCreate,
  OrgUpdate,
  ProjectModels,
  TemplateSummary,
  WorkItemCreate,
  DiffSummary,
  RunEventDto,
  RunChain,
  RunLogs,
  TimelineEntry,
  ConnectorDto,
  ConnectorTestResult,
  BudgetAlert,
  EgressCheck,
  EgressCheckResult,
  EgressDecision,
  EgressOverview,
  EgressRequestCreate,
  EgressRule,
  EgressRuleCreate,
  EgressVerdict,
  CostReport,
  CrossBackendReport,
  DoraReport,
  Edition,
  Integrations,
  Procedure,
  ProcedureSummary,
  FindingPage,
  MeDto,
  Memory,
  MemoryAbReport,
  GatewayKeyInfo,
  MemoryStore,
  MemoryStoreCreate,
  MemoryStorePatch,
  ProjectMemoryCreate,
  ModelMatrix,
  PolicyDef,
  PolicyCeilingState,
  McpGatewayAssertion,
  McpGatewayPut,
  McpGatewayState,
  McpGatewayTest,
  McpGatewayTestRequest,
  OutboundProxyPut,
  OutboundProxyState,
  OutboundProxyTest,
  Outpost,
  OutpostCallPage,
  OutpostCallState,
  OutpostCreate,
  OutpostEnrolmentCreate,
  OutpostEnrolmentCreated,
  OutpostInstance,
  OutpostInstanceApprove,
  OutpostInstanceList,
  OutpostList,
  OutpostRefusalCode,
  NetworkActivity,
  ProjectNetwork,
  ProjectDto,
  ProjectPage,
  ProjectReadiness,
  ProjectUpdate,
  ProvisionStatus,
  ReleasePage,
  Run,
  TrainStatus,
  WorkflowDef,
  WorkflowEditResult,
  WorkflowOperation,
  WorkflowRouting,
  RoutingPreview,
  RoutingPreviewRequest,
  WorkflowSummary,
  WorkflowTemplate,
  WorkflowValidation,
  WorkflowAdopt,
  WorkflowCatalogueEntry,
  WorkflowShare,
  WorkItemDto,
  WorkItemJourney,
  WorkItemPage,
  ReceivedEventPage,
  Trigger,
  TriggerAck,
  TriggerToken,
  MachineAccount,
  MachineAccountCreate,
  MachineTokenCreate,
  AssistantEmbed,
  EmbedToken,
  Conversation,
  ConversationAction,
  ConversationActionList,
  RunContext,
} from "./types";

export const API_BASE = "/api/v1";
export const IS_MOCK = process.env.NEXT_PUBLIC_API_MODE === "mock";
/**
 * The starting organisation, before the session says more (demo mode, first render). It is
 * no longer the foundation's (`varga`, ADR 0048): the demo's sample organisation is `acme`, and a
 * live console without `NEXT_PUBLIC_DEFAULT_ORG` starts on `default` until `/me` names the person's.
 */
export const DEFAULT_ORG = defaultOrg(process.env.NEXT_PUBLIC_DEFAULT_ORG, IS_MOCK);

export function defaultOrg(configured: string | undefined, mock: boolean): string {
  return configured?.trim() || (mock ? "acme" : "default");
}

// ─── the current organisation ───
// The front had one organisation, hard-coded. Projects are addressed to the API as `org:slug` —
// a slug alone is ambiguous as soon as two organisations share a project name, and the API
// refuses it. The session sets the organisation here; pages pass slugs, `qualify` does the rest.
let currentOrg = DEFAULT_ORG;
export function setCurrentOrg(org: string): void {
  currentOrg = org || DEFAULT_ORG;
}
export function orgSlug(): string {
  return currentOrg;
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** A project identifier as the API expects it: a UUID as it is, otherwise `org:slug`. */
export function qualify(id: string): string {
  if (UUID.test(id) || id.includes(":") || id.includes("/")) return id;
  return `${currentOrg}:${id}`;
}

/** An RFC 9457 problem as the API answers it; `correlation_id` ties a 500 to the API's logs (S26-02). */
export type Problem = {
  title?: string;
  detail?: string;
  errors?: unknown[];
  correlation_id?: string;
  /** A machine code on some 409s: `stale_base`, `no_change`, `eval_failed` (S40-12), `egress_required` on the
   * 409 of a governed "add" whose host the organisation has not opened (S39-22). */
  code?: string;
  latest_version?: number;
  latest_author?: string | null;
  verdicts?: EgressVerdict[];
  /** `gateway_in_use` (ADR 0105): the connectors that still go through the MCP gateway; `outpost_in_use`
   * (ADR 0149): the connectors that still route to the outpost. */
  connectors?: string[];
  /** The outpost a 409 of `OutpostConflict` concerns (ADR 0147), when it names one. */
  outpost?: string;
  /** The alias a refusal of a connector at an outpost was for (ADR 0149 §7). */
  endpoint?: string;
  /** `outpost_refused`: the outpost's own code for why it did not make the call (ADR 0147 §10). */
  refusal?: OutpostRefusalCode;
  /** Seconds before asking again, on a 429 (an outpost's quota at discovery, ADR 0148 §10). */
  retry_after?: number;
};

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Problem,
  ) {
    super(problem.detail ?? problem.title ?? `request failed (HTTP ${status})`);
    this.name = "ApiError";
  }

  /** The id to quote to the operators: the problem's, else the `X-Request-Id` the API answered. */
  get correlationId(): string | undefined {
    return this.problem.correlation_id;
  }
}

/**
 * The problem an error response carries. A problem+json body is read as is; any other body (a proxy's
 * HTML page, a plain-text 500) becomes a problem titled with the HTTP status text. On a 5xx the
 * correlation id is appended to the detail, so the console never shows a bare "Internal Server Error"
 * without the id to look for in the logs.
 */
export async function problemOf(response: Response): Promise<Problem> {
  const requestId = response.headers.get("x-request-id") ?? undefined;
  let problem: Problem;
  try {
    const body: unknown = await response.json();
    problem = typeof body === "object" && body !== null ? (body as Problem) : { title: response.statusText };
  } catch {
    problem = { title: response.statusText || `HTTP ${response.status}` };
  }
  if (!problem.correlation_id && requestId) problem = { ...problem, correlation_id: requestId };
  const id = problem.correlation_id;
  if (response.status >= 500 && id && !problem.detail?.includes(id)) {
    const base = problem.detail ?? problem.title ?? `request failed (HTTP ${response.status})`;
    problem = { ...problem, detail: `${base} (correlation id ${id})` };
  }
  return problem;
}

/** On a 401 outside the sign-in page: go there, keeping where one comes from. A framed page (`/embed/`) never
 * navigates: the console's login refuses to be framed, and the embedded component asks the person itself. */
function versLaConnexion(): void {
  if (typeof window === "undefined" || window.location.pathname.startsWith("/login")) return;
  if (window.location.pathname.startsWith("/embed/")) return;
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  // A full navigation, on purpose: the session is dead, and so is the React state.
  window.location.assign(new URL(`/login?next=${next}`, window.location.origin).toString());
}

/**
 * A 401 `step_up_required` is not a dead session: the gesture asks for a RECENT authentication (an
 * action decision, ADR 0030). We go back through the IdP with `reauth=1`, and come back here;
 * sending to `/login` looped, since the session is alive.
 */
function asksForReauthentication(problem: { errors?: unknown[] }): boolean {
  const first = (problem.errors ?? [])[0] as { error?: string } | undefined;
  return first?.error === "step_up_required";
}

function versLaReauthentification(): void {
  if (typeof window === "undefined") return;
  const ici = window.location.pathname + window.location.search;
  window.location.assign(new URL(api.loginUrl(ici, undefined, true), window.location.origin).toString());
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (IS_MOCK) {
    // The fixtures go into the bundle only if demo mode is requested: imported statically, 460
    // lines of fake data went to every user.
    const { mockApi } = await import("@/mocks/data");
    return mockApi<T>(path, init);
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    // A file upload (`FormData`) sets its own type, with its multipart boundary.
    // A raw file (`Blob`) carries the content type its caller gives it (`sectionUpload`).
    headers: {
      ...(init.body instanceof FormData || init.body instanceof Blob
        ? { ...(init.headers ?? {}) }
        : { "Content-Type": "application/json", ...(init.headers ?? {}) }),
      // The embedded component's session in a cross-site frame (S63-12): nothing unless the frame holds one.
      ...embedHeaders(),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const problem = await problemOf(response);
    if (response.status === 401 && isFrameSession()) {
      // A frame under a frame token: its token expired. The frame asks the person again; it never navigates.
      clearEmbedToken();
    } else if (response.status === 401) {
      if (asksForReauthentication(problem)) versLaReauthentification();
      else versLaConnexion();
    }
    throw new ApiError(response.status, problem);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function artifactContent(id: string, ref: string): Promise<Blob> {
  if (IS_MOCK) {
    const { mockArtifactContent } = await import("@/mocks/data");
    return mockArtifactContent(id, ref);
  }
  const response = await fetch(`${API_BASE}/work-items/${id}/artifacts/${encodeURIComponent(ref)}/content`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    let problem: { title?: string; detail?: string } = {};
    try {
      problem = await response.json();
    } catch {
      problem = { title: response.statusText };
    }
    if (response.status === 401) versLaConnexion();
    throw new ApiError(response.status, problem);
  }
  return response.blob();
}

/**
 * The CLI this instance bundles (S62-08), read at the root (`/downloads/cli/manifest.json`), outside
 * `/api/v1`: the install scripts and `choregos self update` read the same file. `null` when the build
 * bundles none (the API's 404), and in the demo, which has no binaries to offer.
 */
async function cliManifest(): Promise<CliManifest | null> {
  if (IS_MOCK) return null;
  const response = await fetch(MANIFEST_PATH, { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw new ApiError(response.status, await problemOf(response));
  return (await response.json()) as CliManifest;
}

export const api = {
  me: () => request<MeDto>("/me"),
  cliManifest,
  /** The embedded component (S63-12, ADR 0139 point 6): who may frame it (public), and its conversation. */
  assistantEmbed: (org: string, slug: string) =>
    request<AssistantEmbed>(`/public/assistants/${org}/${slug}/embed`),
  openAssistantConversation: (org: string, slug: string, context: Record<string, unknown> | null) =>
    request<Conversation>(`/me/assistants/${org}/${slug}/conversations`, {
      method: "POST",
      body: JSON.stringify({ context }),
    }),
  myConversation: (id: string) => request<Conversation>(`/me/conversations/${id}`),
  /** One streamed turn: the raw response, read as server-sent events by `lib/embed.ts`. */
  conversationTurn: (id: string, input: string, signal?: AbortSignal) =>
    fetch(`${API_BASE}/me/conversations/${id}/messages`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...embedHeaders() },
      body: JSON.stringify({ input, stream: true }),
      signal,
    }),
  /** The frame token the sign-in popup hands to the frame (S63-12, API `embed_frame`). */
  mintEmbedToken: (org: string, slug: string) =>
    request<EmbedToken>(`/me/assistants/${org}/${slug}/embed-token`, { method: "POST" }),
  conversationActions: (id: string) => request<ConversationActionList>(`/me/conversations/${id}/actions`),
  confirmConversationAction: (id: string, action: string) =>
    request<ConversationAction>(`/me/conversations/${id}/actions/${action}/confirm`, { method: "POST" }),
  refuseConversationAction: (id: string, action: string) =>
    request<ConversationAction>(`/me/conversations/${id}/actions/${action}/refuse`, { method: "POST" }),
  /** The running edition (ADR 0024): the console shows what it allows itself. */
  edition: () => request<Edition>("/edition"),
  /** Where to plug an MCP client (ADR 0030): the gate's URL, for the Integrations page. */
  integrations: () => request<Integrations>("/integrations"),
  /** The setup procedures (ADR 0067); `for` answers the one best for a target, a preset before the generic one. */
  listProcedures: (query: { for?: string; card_host?: string; preset?: string } = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) if (value) params.set(key, value);
    const q = params.toString();
    return request<ProcedureSummary[]>(`/procedures${q ? `?${q}` : ""}`);
  },
  getProcedure: (id: string) => request<Procedure>(`/procedures/${encodeURIComponent(id)}`),
  /** The URL that opens the session: the API redirects to the IdP (or, in dev, opens directly). */
  /** `tenant` picks the organisation's own identity provider, by sign-in link or by address (ADR 0108). */
  loginUrl: (
    next?: string,
    as?: string,
    reauth = false,
    tenant: { org?: string; loginHint?: string } = {},
    silent = false,
  ) => {
    const params = new URLSearchParams();
    if (next) params.set("redirect_to", next);
    if (as) params.set("as", as);
    if (reauth) params.set("reauth", "1");
    if (silent) params.set("silent", "1");
    if (tenant.org) params.set("org", tenant.org);
    if (tenant.loginHint) params.set("login_hint", tenant.loginHint);
    const q = params.toString();
    return `${API_BASE}/auth/login${q ? `?${q}` : ""}`;
  },
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  orgs: () => request<Org[]>("/orgs"),
  createOrg: (body: OrgCreate) => request<Org>("/orgs", { method: "POST", body: JSON.stringify(body) }),
  /** `PATCH /orgs/{org}`: the name and currency (its administrator), the slug (a platform administrator only). */
  updateOrg: (org: string, body: OrgUpdate) =>
    request<Org>(`/orgs/${encodeURIComponent(org)}`, { method: "PATCH", body: JSON.stringify(body) }),
  /** The organisation's memberships; with `project`, that project's only (S30-06). */
  members: (org: string, project?: string) =>
    request<Membership[]>(`/orgs/${org}/members${project ? `?project=${encodeURIComponent(project)}` : ""}`),
  /** Removes a membership, of the organisation or of the one project named. */
  removeMember: (org: string, userId: string, project?: string | null) =>
    request<void>(`/orgs/${org}/members/${encodeURIComponent(userId)}${project ? `?project=${encodeURIComponent(project)}` : ""}`, {
      method: "DELETE",
    }),
  /** A member's links to their accounts on trackers (S47-14): GitHub only for now. */
  trackerIdentities: (org: string, userId: string) =>
    request<TrackerIdentity[]>(`/orgs/${org}/members/${encodeURIComponent(userId)}/tracker-identities`),
  setTrackerIdentity: (org: string, userId: string, tracker: TrackerIdentity["tracker"], body: { external_id: string; login: string }) =>
    request<TrackerIdentity>(`/orgs/${org}/members/${encodeURIComponent(userId)}/tracker-identities/${tracker}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteTrackerIdentity: (org: string, userId: string, tracker: TrackerIdentity["tracker"]) =>
    request<void>(`/orgs/${org}/members/${encodeURIComponent(userId)}/tracker-identities/${tracker}`, { method: "DELETE" }),
  /** The administration sections that extensions declare, filtered by the API (ADR 0032). */
  adminSections: (org: string) => request<AdminSection[]>(`/ui/admin-sections?org=${encodeURIComponent(org)}`),
  /**
   * A path that a section declares (relative to `/api/v1`, `{org}` and `{id}` already filled in).
   * The console only calls the paths the API gave it, and the API checked at startup that a route
   * serves them.
   */
  sectionCall: <T>(method: string, path: string, body?: unknown) =>
    request<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) }),
  /**
   * Upload a file to a path a section declares in a form field of `format: "asset"` (`x-upload`, S42-13):
   * the RAW body with the file's own content type, answered by `{ ref }`, the value the field then holds.
   */
  sectionUpload: (path: string, file: File) =>
    request<{ ref: string }>(path, {
      method: "POST",
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
    }),
  addMember: (org: string, body: MembershipUpsert) =>
    request<Membership>(`/orgs/${org}/members`, { method: "POST", body: JSON.stringify(body) }),
  /** The organisation's agent registry (ADR 0033): a version is never modified. */
  agents: (org: string) => request<Agent[]>(`/orgs/${org}/agents`),
  agent: (org: string, slug: string) => request<Agent>(`/orgs/${org}/agents/${slug}`),
  createAgent: (org: string, body: AgentCreate, allowEgress = false) =>
    request<Agent>(`/orgs/${org}/agents${allowEgress ? "?allow_egress=true" : ""}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** A model drafts an agent from its description: a proposal, never saved (S39-05). */
  draftAgent: (org: string, body: AgentDraftRequest) =>
    request<AgentDraft>(`/orgs/${org}/agent-drafts`, { method: "POST", body: JSON.stringify(body) }),
  /** A fork: a new agent in the scope asked, whose v1 copies a version of the source (ADR 0061). */
  forkAgent: (org: string, slug: string, body: AgentFork) =>
    request<Agent>(`/orgs/${org}/agents/${slug}/fork`, { method: "POST", body: JSON.stringify(body) }),
  /** Move an agent to another scope (promote a personal or project agent); the server decides who may. */
  changeAgentScope: (org: string, slug: string, body: AgentScopeChange) =>
    request<Agent>(`/orgs/${org}/agents/${slug}/scope`, { method: "POST", body: JSON.stringify(body) }),
  updateAgent: (org: string, slug: string, body: AgentPatch) =>
    request<Agent>(`/orgs/${org}/agents/${slug}`, { method: "PATCH", body: JSON.stringify(body) }),
  /**
   * Suspends the agent, then cancels its runs in flight in every project, closes its open sessions and
   * cancels its conversation turns (ADR 0142 §4); the answer counts what was stopped and what could not be.
   */
  stopAgent: (org: string, slug: string, reason: string) =>
    request<AgentStopResult>(`/orgs/${org}/agents/${slug}/stop`, { method: "POST", body: JSON.stringify({ reason } satisfies StopRequest) }),
  /** Deletes an agent that never served; 409 with the reasons otherwise (retire it instead). */
  deleteAgent: (org: string, slug: string) => request<void>(`/orgs/${org}/agents/${slug}`, { method: "DELETE" }),
  /** Its life (S32-04): activate a draft, deprecate, retire — retiring runs its checklist. */
  changeAgentLifecycle: (org: string, slug: string, body: AgentLifecycleChange) =>
    request<AgentLifecycleResult>(`/orgs/${org}/agents/${slug}/lifecycle`, { method: "POST", body: JSON.stringify(body) }),
  transferAgentOwnership: (org: string, slug: string, email: string) =>
    request<Agent>(`/orgs/${org}/agents/${slug}/owner`, { method: "POST", body: JSON.stringify({ email }) }),
  /** The next version with its message, from the version the author read; 409 `stale_base` or `no_change` otherwise (S40-12). */
  publishAgentVersionWithMessage: (org: string, slug: string, body: AgentVersionPublish, allowEgress = false) =>
    request<AgentVersion>(`/orgs/${org}/agents/${slug}/versions/publish${allowEgress ? "?allow_egress=true" : ""}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Copy version N as the next version; nothing is rewritten (S40-12). */
  restoreAgentVersion: (org: string, slug: string, version: number, body: AgentVersionRestore) =>
    request<AgentVersion>(`/orgs/${org}/agents/${slug}/versions/${version}/restore`, { method: "POST", body: JSON.stringify(body) }),
  publishAgentVersion: (org: string, slug: string, spec: AgentSpec, allowEgress = false) =>
    request<AgentVersion>(`/orgs/${org}/agents/${slug}/versions${allowEgress ? "?allow_egress=true" : ""}`, {
      method: "POST",
      body: JSON.stringify(spec),
    }),
  /** Channels (S40-02, ADR 0071): point production or testing at a revision; the history, the checklist. */
  releaseAgentVersion: (org: string, slug: string, body: AgentReleaseCreate) =>
    request<AgentRelease>(`/orgs/${org}/agents/${slug}/releases`, { method: "POST", body: JSON.stringify(body) }),
  agentReleases: (org: string, slug: string, limit = 50) =>
    request<AgentRelease[]>(`/orgs/${org}/agents/${slug}/releases?limit=${limit}`),
  agentReleaseReadiness: (org: string, slug: string, version: number, channel: "stable" | "candidate") =>
    request<AgentReleaseReadiness>(`/orgs/${org}/agents/${slug}/versions/${version}/readiness?channel=${channel}`),
  setAgentReleasePolicy: (org: string, slug: string, body: AgentReleasePolicy) =>
    request<Agent>(`/orgs/${org}/agents/${slug}/release-policy`, { method: "PUT", body: JSON.stringify(body) }),
  /** A live evaluation: the revision's `live` cases run as capped trials in a project (S40-02d). */
  startAgentEvaluation: (org: string, slug: string, version: number, project: string) =>
    request<AgentEvaluation>(`/orgs/${org}/agents/${slug}/versions/${version}/evaluations`, {
      method: "POST",
      body: JSON.stringify({ project }),
    }),
  agentEvaluations: (org: string, slug: string, version: number) =>
    request<AgentEvaluation[]>(`/orgs/${org}/agents/${slug}/versions/${version}/evaluations`),
  agentExposure: (org: string, slug: string) => request<AgentExposure>(`/orgs/${org}/agents/${slug}/exposure`),
  decideAgentRelease: (org: string, slug: string, id: string, body: AgentReleaseDecision) =>
    request<AgentRelease>(`/orgs/${org}/agents/${slug}/releases/${id}/decision`, { method: "POST", body: JSON.stringify(body) }),
  /** The approvers' inbox: releases of the agents one may see, by status. */
  orgAgentReleases: (org: string, status?: AgentRelease["status"]) =>
    request<AgentRelease[]>(`/orgs/${org}/agent-releases${status ? `?status=${status}` : ""}`),
  projectAgentChannel: (id: string) => request<ProjectAgentChannel>(`/projects/${qualify(id)}/agent-channel`),
  setProjectAgentChannel: (id: string, channel: ProjectAgentChannel["channel"]) =>
    request<ProjectAgentChannel>(`/projects/${qualify(id)}/agent-channel`, { method: "PUT", body: JSON.stringify({ channel }) }),
  agentMetrics: (org: string, slug: string, days = 30) =>
    request<AgentMetrics>(`/orgs/${org}/agents/${slug}/metrics?days=${days}`),
  /** Remote agents over A2A (ADR 0062): the organisation's credentials, and the reading of a card. */
  remoteCredentials: (org: string) => request<RemoteCredential[]>(`/orgs/${org}/remote-credentials`),
  createRemoteCredential: (org: string, body: RemoteCredentialCreate) =>
    request<RemoteCredential>(`/orgs/${org}/remote-credentials`, { method: "POST", body: JSON.stringify(body) }),
  authorizeRemoteCredential: (org: string, name: string) =>
    request<RemoteCredentialAuthorization>(`/orgs/${org}/remote-credentials/${name}/authorize`, { method: "POST" }),
  revokeRemoteCredential: (org: string, name: string) =>
    request<void>(`/orgs/${org}/remote-credentials/${name}`, { method: "DELETE" }),
  discoverRemoteAgent: (org: string, cardUrl: string, allowEgress = false) =>
    request<RemoteAgentDiscovery>(`/orgs/${org}/agents/discover${allowEgress ? "?allow_egress=true" : ""}`, {
      method: "POST",
      body: JSON.stringify({ card_url: cardUrl }),
    }),
  /** The platform's catalogue (ADR 0040): what it offers, what is installed from it. */
  agentCatalogue: (org: string) => request<AgentCatalogueEntry[]>(`/orgs/${org}/agent-catalogue`),
  installCatalogueAgent: (org: string, slug: string, upgrade = false) =>
    request<Agent>(`/orgs/${org}/agent-catalogue/${slug}/install`, {
      method: "POST",
      body: JSON.stringify({ upgrade }),
    }),
  /** A client in one click: its external agent, an `mcp:*` token returned once, or its OAuth client. */
  connectCatalogueClient: (org: string, slug: string, readOnly = false) =>
    request<AgentCatalogueConnection>(`/orgs/${org}/agent-catalogue/${slug}/connect`, {
      method: "POST",
      body: JSON.stringify({ read_only: readOnly }),
    }),
  /** Every client attached to an agent of the organisation, with its agent: one request (S30-04). */
  orgAgentCredentials: (org: string) => request<AgentCredential[]>(`/orgs/${org}/agent-credentials`),
  /** The registry's columns — runs, success rate, cost — for every agent at once (S30-04). */
  agentMetricsList: (org: string, days = 30) => request<AgentMetricsSummary[]>(`/orgs/${org}/agent-metrics?days=${days}`),
  /** A project's implicit agents, computed by the API (S30-04). */
  implicitAgents: (id: string) => request<ImplicitAgent[]>(`/projects/${qualify(id)}/implicit-agents`),
  /** Every agent the project's active workflows run, named or built in (S47-39). */
  workflowAgents: (id: string) => request<WorkflowAgent[]>(`/projects/${qualify(id)}/workflow-agents`),
  /** Who a person step addressed to a group waits for, and who of them may answer in the project. */
  groupMembers: (id: string, group: string) =>
    request<GroupMembers>(`/projects/${qualify(id)}/groups/${encodeURIComponent(group)}/members`),
  /** Where a workflow's open work items wait, and what each step's runs did over `days` days. */
  workflowActivity: (id: string, name: string, days = 30) =>
    request<WorkflowActivity>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}/activity?days=${days}`),
  /** A step's decisions measured from the ledger, per question and model version (ADR 0121, S52-05). */
  stepCalibration: (id: string, name: string, step: string, target = 0.9) =>
    request<StepCalibration>(
      `/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}/steps/${encodeURIComponent(step)}/calibration?target=${target}`,
    ),
  /** How well the decision model that routes the project's work items chose their workflow (ADR 0123 §4, S52-11). */
  routingCalibration: (id: string, target = 0.9) =>
    request<RoutingCalibration>(`/projects/${qualify(id)}/workflow-routing/calibration?target=${target}`),
  /** What an `agent` actor that names no registry agent runs: its prompt, model, backend, limits and tools. */
  describeBuiltinAgent: (id: string, actor: BuiltinAgentQuery) =>
    request<BuiltinAgent>(
      `/projects/${qualify(id)}/builtin-agent?${new URLSearchParams(
        Object.entries(actor).flatMap(([key, value]) => (value == null || value === "" ? [] : [[key, String(value)]])),
      )}`,
    ),
  /** Which agent version a run would load for each reference, through the runtime's resolver (S39-13). */
  resolveProjectAgents: (id: string, refs: string[]) =>
    request<AgentResolution[]>(
      `/projects/${qualify(id)}/agent-resolution?${refs.map((ref) => `ref=${encodeURIComponent(ref)}`).join("&")}`,
    ),
  /** Try a draft version once, under a capped budget (S32-03). */
  startAgentTrial: (org: string, body: AgentTrialCreate) =>
    request<AgentTrial>(`/orgs/${org}/agent-trials`, { method: "POST", body: JSON.stringify(body) }),
  /** An agent's latest runs (S30-05). */
  agentRuns: (org: string, slug: string, limit = 50) => request<Run[]>(`/orgs/${org}/agents/${slug}/runs?limit=${limit}`),
  /** What a version of the agent reaches in one project, the five components of its definition (ADR 0127). */
  agentAccess: (org: string, slug: string, project: string, version?: number) => {
    const query = new URLSearchParams({ project });
    if (version != null) query.set("version", String(version));
    return request<AgentAccess>(`/orgs/${org}/agents/${slug}/access?${query}`);
  },
  agentCredentials: (org: string, slug: string) =>
    request<AgentCredential[]>(`/orgs/${org}/agents/${slug}/credentials`),
  attachAgentToken: (org: string, slug: string, tokenId: string) =>
    request<AgentCredential>(`/orgs/${org}/agents/${slug}/credentials`, {
      method: "POST",
      body: JSON.stringify({ kind: "token", token_id: tokenId }),
    }),
  detachAgentCredential: (org: string, slug: string, id: string) =>
    request<void>(`/orgs/${org}/agents/${slug}/credentials/${id}`, { method: "DELETE" }),
  projectAgents: (id: string) => request<ProjectAgent[]>(`/projects/${qualify(id)}/agents`),
  pinProjectAgent: (id: string, slug: string, body: ProjectAgentPut) =>
    request<ProjectAgent>(`/projects/${qualify(id)}/agents/${slug}`, { method: "PUT", body: JSON.stringify(body) }),
  unpinProjectAgent: (id: string, slug: string) =>
    request<void>(`/projects/${qualify(id)}/agents/${slug}`, { method: "DELETE" }),
  /** The skill library: a folder, imported as a zip, versioned, never rewritten. */
  skills: (org: string) => request<Skill[]>(`/orgs/${org}/skills`),
  /** The catalogue of default skills and what each means in this organisation (ADR 0065). */
  skillCatalogue: (org: string) => request<SkillCatalogue>(`/orgs/${org}/skill-catalogue`),
  installSkillCatalogueEntry: (org: string, id: string, allowEgress = false) =>
    request<Skill>(`/orgs/${org}/skill-catalogue/${encodeURIComponent(id)}/install`, {
      method: "POST",
      ...(allowEgress && { body: JSON.stringify({ allow_egress: true }) }),
    }),
  /** A hosted agent's source zip (ADR 0080, S41-07), checked and stored under its sha256; a zip version names it. */
  uploadHostedSource: (org: string, archive: Blob) =>
    request<HostedSource>(`/orgs/${org}/hosted-sources`, {
      method: "POST",
      body: archive,
      headers: { "Content-Type": "application/zip" },
    }),
  skill: (org: string, slug: string) => request<Skill>(`/orgs/${org}/skills/${slug}`),
  skillVersion: (org: string, slug: string, version: number) =>
    request<SkillVersion>(`/orgs/${org}/skills/${slug}/versions/${version}`),
  importSkill: (org: string, archive: Blob) =>
    request<Skill>(`/orgs/${org}/skills/import`, {
      method: "POST",
      body: archive,
      headers: { "Content-Type": "application/zip" },
    }),
  /** Knowledge bases (ADR 0051): a curated corpus, converted to markdown outside the request. */
  knowledgeBases: (org: string) => request<KnowledgeBase[]>(`/orgs/${org}/knowledge`),
  knowledgeBase: (org: string, slug: string) => request<KnowledgeBase>(`/orgs/${org}/knowledge/${slug}`),
  createKnowledgeBase: (org: string, body: KnowledgeBaseCreate) =>
    request<KnowledgeBase>(`/orgs/${org}/knowledge`, { method: "POST", body: JSON.stringify(body) }),
  /** A base owned by the project (S39-14): scoped to it, curated by its owners. */
  createProjectKnowledgeBase: (project: string, body: ProjectKnowledgeBaseCreate) =>
    request<KnowledgeBase>(`/projects/${encodeURIComponent(project)}/knowledge`, { method: "POST", body: JSON.stringify(body) }),
  updateKnowledgeBase: (org: string, slug: string, body: KnowledgeBasePatch) =>
    request<KnowledgeBase>(`/orgs/${org}/knowledge/${slug}`, { method: "PATCH", body: JSON.stringify(body) }),
  /** Who holds a role on a base (S42-11): readable and replaceable by callers who can share it. */
  knowledgeGrants: (org: string, slug: string) => request<KnowledgeGrants>(`/orgs/${org}/knowledge/${slug}/grants`),
  putKnowledgeGrants: (org: string, slug: string, grants: KnowledgeGrant[]) =>
    request<KnowledgeGrants>(`/orgs/${org}/knowledge/${slug}/grants`, { method: "PUT", body: JSON.stringify({ grants }) }),
  uploadKnowledgeDocument: (org: string, slug: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<KnowledgeDocument>(`/orgs/${org}/knowledge/${slug}/documents`, { method: "POST", body: form });
  },
  knowledgeDocument: (org: string, slug: string, id: string) =>
    request<KnowledgeDocument>(`/orgs/${org}/knowledge/${slug}/documents/${id}`),
  deleteKnowledgeDocument: (org: string, slug: string, id: string) =>
    request<void>(`/orgs/${org}/knowledge/${slug}/documents/${id}`, { method: "DELETE" }),
  convertKnowledgeDocument: (org: string, slug: string, id: string) =>
    request<KnowledgeDocument>(`/orgs/${org}/knowledge/${slug}/documents/${id}/convert`, { method: "POST" }),
  /** From a repository at a pinned commit (ADR 0050): the preview resolves the branch, the import pins the SHA. */
  previewSkillsFromGit: (org: string, body: SkillGitPreviewRequest) =>
    request<SkillGitPreview>(`/orgs/${org}/skills/import-git/preview`, { method: "POST", body: JSON.stringify(body) }),
  importSkillsFromGit: (org: string, body: SkillGitImport) =>
    request<Skill[]>(`/orgs/${org}/skills/import-git`, { method: "POST", body: JSON.stringify(body) }),
  skillSources: (org: string) => request<SkillSources>(`/orgs/${org}/skill-sources`),
  checkSkillUpdate: (org: string, slug: string) => request<SkillUpdate>(`/orgs/${org}/skills/${slug}/update`),
  approveSkillVersion: (org: string, slug: string, version: number, force?: { reason: string }) =>
    request<SkillVersion>(`/orgs/${org}/skills/${slug}/versions/${version}/approve`, {
      method: "POST",
      ...(force ? { body: JSON.stringify({ force: true, reason: force.reason }) } : {}),
    }),

  searchKnowledgeBase: (org: string, slug: string, query: string, k = 5) =>
    request<KnowledgeSearchResult>(`/orgs/${org}/knowledge/${slug}/search`, {
      method: "POST",
      body: JSON.stringify({ query, k }),
    }),
  reindexKnowledgeBase: (org: string, slug: string) =>
    request<KnowledgeBase>(`/orgs/${org}/knowledge/${slug}/reindex`, { method: "POST" }),
  putKnowledgeEvalSet: (org: string, slug: string, cases: KnowledgeEvalCase[]) =>
    request<KnowledgeBase>(`/orgs/${org}/knowledge/${slug}/eval`, { method: "PUT", body: JSON.stringify({ cases }) }),
  distillKnowledgeBase: (org: string, slug: string, body?: KnowledgeDistillRequest) =>
    request<KnowledgeBase>(`/orgs/${org}/knowledge/${slug}/distill`, {
      method: "POST",
      ...(body ? { body: JSON.stringify(body) } : {}),
    }),
  stopKnowledgeDistillation: (org: string, slug: string) =>
    request<KnowledgeBase>(`/orgs/${org}/knowledge/${slug}/distill/stop`, { method: "POST" }),
  relaunchKnowledgeDistillation: (org: string, slug: string, body?: KnowledgeDistillRequest) =>
    request<KnowledgeBase>(`/orgs/${org}/knowledge/${slug}/distill/relaunch`, {
      method: "POST",
      ...(body ? { body: JSON.stringify(body) } : {}),
    }),
  estimateKnowledgeDistillation: (org: string, slug: string, exclude: string[] = []) =>
    request<KnowledgeDistillEstimate>(
      `/orgs/${org}/knowledge/${slug}/distill/estimate${exclude.length ? `?${exclude.map((e) => `exclude=${encodeURIComponent(e)}`).join("&")}` : ""}`,
    ),
  runKnowledgeEval: (org: string, slug: string) =>
    request<KnowledgeBase>(`/orgs/${org}/knowledge/${slug}/eval/run`, { method: "POST" }),
  createKnowledgeSource: (org: string, slug: string, body: KnowledgeSourceCreate) =>
    request<KnowledgeSource>(`/orgs/${org}/knowledge/${slug}/sources`, { method: "POST", body: JSON.stringify(body) }),
  updateKnowledgeSource: (org: string, slug: string, id: string, body: KnowledgeSourcePatch) =>
    request<KnowledgeSource>(`/orgs/${org}/knowledge/${slug}/sources/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  syncKnowledgeBase: (org: string, slug: string) =>
    request<KnowledgeBase>(`/orgs/${org}/knowledge/${slug}/sync`, { method: "POST" }),
  syncKnowledgeSource: (org: string, slug: string, id: string) =>
    request<KnowledgeSource>(`/orgs/${org}/knowledge/${slug}/sources/${id}/sync`, { method: "POST" }),
  deleteKnowledgeSource: (org: string, slug: string, id: string) =>
    request<void>(`/orgs/${org}/knowledge/${slug}/sources/${id}`, { method: "DELETE" }),
  myTokens: () => request<ApiToken[]>("/me/tokens"),
  createToken: (body: ApiTokenCreate) =>
    request<ApiTokenCreated>("/me/tokens", { method: "POST", body: JSON.stringify(body) }),
  revokeToken: (id: string) => request<void>(`/me/tokens/${id}`, { method: "DELETE" }),
  mySessions: () => request<BrowserSession[]>("/me/sessions"),
  endSession: (id: string) => request<void>(`/me/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  endOtherSessions: () => request<EndedSessions>("/me/sessions/end-others", { method: "POST" }),
  templates: () => request<TemplateSummary[]>("/templates"),
  connectorTypes: () => request<ConnectorType[]>("/connectors/types"),
  platformModels: () => request<GatewayModel[]>("/platform/models"),
  /** The organisation's model catalogue and what an agent may use of it (ADR 0060). */
  modelCatalogue: (org: string) => request<ModelCatalogue>(`/orgs/${org}/models`),
  usableModels: (org: string, query: { role?: string; usage?: string } = {}) => {
    const params = new URLSearchParams();
    if (query.role) params.set("role", query.role);
    if (query.usage) params.set("usage", query.usage);
    const suffix = params.toString();
    return request<UsableModel[]>(`/orgs/${org}/models/usable${suffix ? `?${suffix}` : ""}`);
  },
  putModelCatalogueSettings: (org: string, body: ModelCatalogueSettings) =>
    request<ModelCatalogueSettings>(`/orgs/${org}/models/settings`, { method: "PUT", body: JSON.stringify(body) }),
  syncModelCatalogue: (org: string) => request<ModelSyncResult>(`/orgs/${org}/models/sync`, { method: "POST" }),
  addModelCatalogueEntry: (org: string, body: ModelCatalogueEntryCreate) =>
    request<ModelCatalogueEntry>(`/orgs/${org}/models`, { method: "POST", body: JSON.stringify(body) }),
  updateModelCatalogueEntry: (org: string, id: string, body: ModelCataloguePatch) =>
    request<ModelCatalogueEntry>(`/orgs/${org}/models/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteModelCatalogueEntry: (org: string, id: string) =>
    request<void>(`/orgs/${org}/models/${id}`, { method: "DELETE" }),
  previewModelCuration: (org: string) => request<ModelCuratePreview>(`/orgs/${org}/models/curate-preview`),
  backends: () => request<AgentBackendInfo[]>("/platform/backends"),
  /** Enable or disable a coding-agent backend in the platform catalogue (platform administrators only). */
  putPlatformBackend: (body: AgentBackendUpdate) =>
    request<AgentBackendInfo>("/platform/backends", { method: "PUT", body: JSON.stringify(body) }),
  /** The images an operator named for agents written in code: names only (ADR 0063). */
  agentImages: () => request<AgentImageInfo[]>("/platform/agent-images"),
  executors: () => request<ExecutorInfo[]>("/platform/executors"),
  /** How people sign in to this instance: the deployment's sign-in configuration, secret-free (S42-03). */
  platformIdentity: () => request<PlatformIdentity>("/platform/identity"),
  models: (id: string) => request<ProjectModels>(`/projects/${qualify(id)}/models`),
  putModels: (id: string, body: ProjectModels) =>
    request<ProjectModels>(`/projects/${qualify(id)}/models`, { method: "PUT", body: JSON.stringify(body) }),
  putPolicy: (id: string, yaml: string) =>
    request<PolicyDef>(`/projects/${qualify(id)}/policy`, { method: "PUT", body: JSON.stringify({ yaml }) }),
  createWorkItem: (id: string, body: WorkItemCreate) =>
    request<WorkItemDto>(`/projects/${qualify(id)}/work-items`, { method: "POST", body: JSON.stringify(body) }),
  suspendProject: (id: string) => request<ProjectDto>(`/projects/${qualify(id)}/suspend`, { method: "POST" }),
  deleteProject: (id: string) => request<void>(`/projects/${qualify(id)}`, { method: "DELETE" }),
  abortRelease: (releaseId: string, reason: string) =>
    request<unknown>(`/releases/${releaseId}/abort`, { method: "POST", body: JSON.stringify({ reason }) }),
  transcript: (runId: string) => request<ArtifactRef>(`/runs/${runId}/transcript`),
  /** The runner pod's log tail and termination, redacted: live while it runs, archived after (S36-03). */
  runLogs: (runId: string) => request<RunLogs>(`/runs/${runId}/logs`),

  projects: (org: string = DEFAULT_ORG) => request<ProjectPage>(`/orgs/${org}/projects`),
  project: (id: string) => request<ProjectDto>(`/projects/${qualify(id)}`),
  /** What the project still lacks to run, computed by the API from stored state (S40-06). */
  projectReadiness: (id: string) => request<ProjectReadiness>(`/projects/${qualify(id)}/readiness`),
  /** `PATCH /projects/{id}`: the name and/or the WHOLE configuration (the API validates it as a `ProjectConfig`). */
  updateProject: (id: string, body: ProjectUpdate) =>
    request<ProjectDto>(`/projects/${qualify(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  createProject: (org: string, body: unknown) =>
    request<ProjectDto>(`/orgs/${org}/projects`, { method: "POST", body: JSON.stringify(body) }),
  provision: (id: string) => request<ProvisionStatus>(`/projects/${qualify(id)}/provision`, { method: "POST", body: "{}" }),
  provisionStatus: (id: string) => request<ProvisionStatus>(`/projects/${qualify(id)}/provision`),

  connectors: (id: string) => request<ConnectorDto[]>(`/projects/${qualify(id)}/connectors`),
  runAccess: (id: string) =>
    request<{
      events: number;
      inferred?: number;
      refusals: number;
      tool_cost_eur?: number;
      access: { nature: string; target: string; requests: number; refusals: number; reasons?: string[] }[];
      chain?: RunChain;
    }>(`/runs/${id}/access`),
  projectTools: (id: string) =>
    request<{
      allows_all: boolean;
      tools: {
        name: string;
        description: string;
        provider: string;
        categories?: string[];
        price_eur?: number;
        needs_credential?: boolean;
        source?: string;
        groups?: string[];
        allowed: boolean;
      }[];
    }>(`/projects/${qualify(id)}/tools`),
  /** The governed actions (ADR 0035): the decision inbox, and each action with its journal. */
  orgActions: (org: string, status?: string) =>
    request<Action[]>(`/orgs/${org}/actions${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  projectActions: (id: string) => request<Action[]>(`/projects/${qualify(id)}/actions`),
  projectAction: (id: string, actionId: string) => request<Action>(`/projects/${qualify(id)}/actions/${actionId}`),
  /** An approval requires a recent session: a 401 `step_up_required` goes back through the IdP. */
  decideAction: (id: string, actionId: string, body: { decision: "approve" | "reject"; reason?: string }) =>
    request<Action>(`/projects/${qualify(id)}/actions/${actionId}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /**
   * The organisation's outposts (ADR 0147): places a person enrols, which only dial out. Every route is
   * `outpost:manage`, the organisation administrator's. The platform stores no address and no
   * credential of an outpost's side, so none is ever here.
   */
  outposts: (org: string) => request<OutpostList>(`/orgs/${org}/outposts`),
  createOutpost: (org: string, body: OutpostCreate) =>
    request<Outpost>(`/orgs/${org}/outposts`, { method: "POST", body: JSON.stringify(body) }),
  outpost: (org: string, slug: string) => request<Outpost>(`/orgs/${org}/outposts/${slug}`),
  /** Refused (409) while connectors still route to it (`outpost_in_use`), then while it is not revoked. */
  deleteOutpost: (org: string, slug: string) => request<void>(`/orgs/${org}/outposts/${slug}`, { method: "DELETE" }),
  pauseOutpost: (org: string, slug: string) => request<Outpost>(`/orgs/${org}/outposts/${slug}/pause`, { method: "POST" }),
  resumeOutpost: (org: string, slug: string) => request<Outpost>(`/orgs/${org}/outposts/${slug}/resume`, { method: "POST" }),
  /** Final: every instance is revoked and the epoch moves. */
  revokeOutpost: (org: string, slug: string) => request<Outpost>(`/orgs/${org}/outposts/${slug}/revoke`, { method: "POST" }),
  /** An enrolment token for one instance, shown once, with the door's origin and the keys to pin (ADR 0148). */
  createOutpostEnrolment: (org: string, slug: string, body: OutpostEnrolmentCreate = {}) =>
    request<OutpostEnrolmentCreated>(`/orgs/${org}/outposts/${slug}/enrolments`, { method: "POST", body: JSON.stringify(body) }),
  outpostInstances: (org: string, slug: string) => request<OutpostInstanceList>(`/orgs/${org}/outposts/${slug}/instances`),
  /**
   * A person approves a pending instance by sending the fingerprint they compared. It needs a recent
   * session, as a decision on an action does: a 401 `step_up_required` goes back through the IdP.
   */
  approveOutpostInstance: (org: string, slug: string, instanceId: string, body: OutpostInstanceApprove) =>
    request<OutpostInstance>(`/orgs/${org}/outposts/${slug}/instances/${instanceId}/approve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Final: the instance's key does not come back, and the outpost's epoch moves. */
  revokeOutpostInstance: (org: string, slug: string, instanceId: string) =>
    request<OutpostInstance>(`/orgs/${org}/outposts/${slug}/instances/${instanceId}/revoke`, { method: "POST" }),
  /** The recent calls routed to an outpost, newest first: digests only, never arguments or a result. */
  outpostCalls: (org: string, slug: string, params: { state?: OutpostCallState; cursor?: string; limit?: number } = {}) =>
    request<OutpostCallPage>(
      `/orgs/${org}/outposts/${slug}/calls${query(compact({ state: params.state, cursor: params.cursor, limit: params.limit?.toString() }))}`,
    ),
  /** The organisation's connectors and the policy of each operation (ADR 0034). */
  orgConnectors: (org: string) => request<OrgConnector[]>(`/orgs/${org}/connectors`),
  createOrgConnector: (org: string, body: OrgConnectorCreate) =>
    request<OrgConnector>(`/orgs/${org}/connectors`, { method: "POST", body: JSON.stringify(body) }),
  deleteOrgConnector: (org: string, name: string) =>
    request<void>(`/orgs/${org}/connectors/${name}`, { method: "DELETE" }),
  /** Edit a declared connector: its configuration, and its secrets by reference (ADR 0058). */
  updateOrgConnector: (org: string, name: string, body: OrgConnectorUpdate) =>
    request<OrgConnector>(`/orgs/${org}/connectors/${name}`, { method: "PATCH", body: JSON.stringify(body) }),
  /** A secret is typed once and never read back: the answer says it is set, not what it is. */
  setOrgConnectorSecret: (org: string, name: string, field: string, value: string) =>
    request<OrgConnector>(`/orgs/${org}/connectors/${name}/secrets/${field}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  clearOrgConnectorSecret: (org: string, name: string, field: string) =>
    request<OrgConnector>(`/orgs/${org}/connectors/${name}/secrets/${field}`, { method: "DELETE" }),
  updateConnectorOperation: (org: string, name: string, operation: string, body: OperationPatch) =>
    request<ConnectorOperation>(`/orgs/${org}/connectors/${name}/operations/${encodeURIComponent(operation)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /** The catalogue of well-known MCP servers and what each means in this organisation (ADR 0065). */
  mcpCatalogue: (org: string) => request<McpCatalogue>(`/orgs/${org}/mcp-catalogue`),
  installMcpCatalogueEntry: (org: string, id: string, body: McpCatalogueInstall) =>
    request<OrgConnector>(`/orgs/${org}/mcp-catalogue/${encodeURIComponent(id)}/install`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** The plugins installed in the organisation (ADR 0093): any member reads them. */
  plugins: (org: string) => request<PluginList>(`/orgs/${org}/plugins`),
  /** The deployment's plugin sources, each with its trust policy and what its index offers. */
  pluginSources: (org: string) => request<PluginSources>(`/orgs/${org}/plugin-sources`),
  /**
   * Installs a plugin: 201 with the installed plugin, or 202 `pending_approval` when it brings a server or
   * a high-risk skill (ADR 0050) — the two bodies are told apart by `status` (see `isPending`).
   */
  installPlugin: (org: string, body: PluginInstall) =>
    request<InstalledPlugin | PluginInstallPending>(`/orgs/${org}/plugins/install`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Moves an installed plugin to another version: 200 with what changed, or 202 as for an install. */
  upgradePlugin: (org: string, name: string, body: PluginUpgrade = {}) =>
    request<PluginUpgradeResult | PluginInstallPending>(`/orgs/${org}/plugins/${encodeURIComponent(name)}/upgrade`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Uninstalls a plugin; the answer says what was removed and what stayed as the organisation's own. */
  uninstallPlugin: (org: string, name: string) =>
    request<PluginUninstallResult>(`/orgs/${org}/plugins/${encodeURIComponent(name)}`, { method: "DELETE" }),
  /** The installs and upgrades that wait, or waited, for a second administrator. */
  pluginApprovals: (org: string) => request<PluginApprovalList>(`/orgs/${org}/plugin-approvals`),
  /** Another administrator than the one who asked approves: 403 for the same one. */
  approvePlugin: (org: string, id: string) =>
    request<PluginApproval>(`/orgs/${org}/plugin-approvals/${encodeURIComponent(id)}/approve`, { method: "POST" }),
  /** The connector types of the registry, by family, with the caller's rights (ADR 0074). */
  connectorCatalogue: (org: string) => request<ConnectorCatalogue>(`/orgs/${org}/connector-catalogue`),
  /** The organisation's connectors as one project sees them: on or off, and may the caller switch. */
  projectOrgConnectors: (id: string) => request<ProjectOrgConnector[]>(`/projects/${qualify(id)}/org-connectors`),
  enableOrgConnector: (id: string, name: string) =>
    request<ProjectOrgConnector>(`/projects/${qualify(id)}/org-connectors/${name}/enablement`, { method: "PUT" }),
  disableOrgConnector: (id: string, name: string) =>
    request<void>(`/projects/${qualify(id)}/org-connectors/${name}/enablement`, { method: "DELETE" }),
  /** An MCP server lists its tools; a new one is born closed, a schema that drifts closes it again. */
  discoverConnectorOperations: (org: string, name: string) =>
    request<ConnectorDiscovery>(`/orgs/${org}/connectors/${name}/discover`, { method: "POST" }),
  projectOperations: (id: string) => request<ProjectOperation[]>(`/projects/${qualify(id)}/operations`),
  tightenProjectOperation: (id: string, connector: string, operation: string, policy: string) =>
    request<ProjectOperation>(
      `/projects/${qualify(id)}/operations/${connector}/${encodeURIComponent(operation)}`,
      { method: "PUT", body: JSON.stringify({ policy }) },
    ),
  relaxProjectOperation: (id: string, connector: string, operation: string) =>
    request<void>(`/projects/${qualify(id)}/operations/${connector}/${encodeURIComponent(operation)}`, {
      method: "DELETE",
    }),
  /** What the project's workflows require of its connectors, and why (ADR 0034). */
  projectRequirements: (id: string) => request<ProjectRequirement[]>(`/projects/${qualify(id)}/requirements`),
  putConnector: (id: string, kind: string, body: unknown) =>
    request<ConnectorDto>(`/projects/${qualify(id)}/connectors/${kind}`, { method: "PUT", body: JSON.stringify(body) }),
  setConnectorSecret: (id: string, kind: string, field: string, value: string) =>
    request<ConnectorDto>(`/projects/${qualify(id)}/connectors/${kind}/secrets/${field}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  clearConnectorSecret: (id: string, kind: string, field: string) =>
    request<ConnectorDto>(`/projects/${qualify(id)}/connectors/${kind}/secrets/${field}`, { method: "DELETE" }),
  testConnector: (id: string, kind: string) =>
    request<ConnectorTestResult>(`/projects/${qualify(id)}/connectors/${kind}/test`, { method: "POST" }),

  /** The Choregos GitHub App as the organisation sees it: its installations, never an installation id to type (ADR 0073). */
  githubStatus: (org: string) => request<GitHubAppStatus>(`/orgs/${org}/github`),
  githubRefresh: (org: string) => request<GitHubAppStatus>(`/orgs/${org}/github/refresh`, { method: "POST" }),
  /** The repositories of the installations the organisation claimed, filtered by `q`. */
  githubRepositories: (org: string, q = "") =>
    request<GitHubRepositoryPage>(`/orgs/${org}/github/repositories${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  githubInstallLink: (org: string, body: GitHubInstallLinkRequest) =>
    request<GitHubInstallLink>(`/orgs/${org}/github/install-link`, { method: "POST", body: JSON.stringify(body) }),
  /** The instance's GitHub App and where it comes from: the deployment's settings or the console (S67-31, ADR 0161). */
  platformGitHubApp: () => request<GitHubAppRegistrationStatus>("/platform/github-app"),
  /** Step 1 of GitHub's App Manifest flow: the form the console posts to github.com. */
  startGitHubAppManifest: (body: GitHubAppManifestRequest) =>
    request<GitHubAppManifestStart>("/platform/github-app/manifest", { method: "POST", body: JSON.stringify(body) }),
  /** Forget the App created from the console (it stays on GitHub). */
  forgetPlatformGitHubApp: () => request<void>("/platform/github-app", { method: "DELETE" }),

  workflow: (id: string) => request<WorkflowDef>(`/projects/${qualify(id)}/workflow`),
  putWorkflow: (id: string, yaml: string) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflow`, { method: "PUT", body: JSON.stringify({ yaml }) }),
  // Several workflows per project, each by its name (ADR 0031).
  workflows: (id: string) => request<WorkflowSummary[]>(`/projects/${qualify(id)}/workflows`),
  workflowNamed: (id: string, name: string) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}`),
  /** Publishes the next version; `baseVersion` is the one that was read, and if stale the API answers 409. */
  putWorkflowNamed: (id: string, name: string, yaml: string, baseVersion?: number) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ yaml, base_version: baseVersion ?? null }),
    }),
  /** Creates a workflow: a name already taken, active or not, answers 409 and nothing is published (#336). */
  createWorkflow: (id: string, name: string, yaml: string) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ yaml, create_only: true }),
    }),
  workflowVersions: (id: string, name: string) =>
    request<WorkflowDef[]>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}/versions`),
  /** Republishes a past version as the next one: nothing is rewritten, the history grows. */
  restoreWorkflowVersion: (id: string, name: string, version: number) =>
    request<WorkflowDef>(
      `/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}/versions/${version}/restore`,
      { method: "POST" },
    ),
  workflowRouting: (id: string) => request<WorkflowRouting>(`/projects/${qualify(id)}/workflow-routing`),
  /** The default and the rules, as one block: the API refuses an unknown or inactive workflow (422). */
  putWorkflowRouting: (id: string, routing: WorkflowRouting) =>
    request<WorkflowRouting>(`/projects/${qualify(id)}/workflow-routing`, {
      method: "PUT",
      body: JSON.stringify(routing),
    }),
  /** Where a work item would be born, and why (ADR 0116); nothing is created, a model call is booked. */
  previewWorkflowRouting: (id: string, body: RoutingPreviewRequest) =>
    request<RoutingPreview>(`/projects/${qualify(id)}/workflow-routing/preview`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** No new ticket; the API refuses the default and the target of a rule (409). */
  deactivateWorkflow: (id: string, name: string) =>
    request<void>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}/deactivate`, { method: "POST" }),
  workflowTemplates: () => request<WorkflowTemplate[]>("/workflows/templates"),
  /** What the organisation can adopt, with what each needs and where its agents resolve (ADR 0111). */
  workflowCatalogue: (org: string) => request<WorkflowCatalogueEntry[]>(`/orgs/${org}/workflow-catalogue`),
  /** Copies a catalogue entry into the project; an unresolved agent is a 422, a taken name a 409. */
  adoptWorkflow: (id: string, body: WorkflowAdopt) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflow-adoptions`, { method: "POST", body: JSON.stringify(body) }),
  /** An organisation administrator shares a project's workflow version to the catalogue. */
  shareWorkflow: (org: string, body: WorkflowShare) =>
    request<WorkflowCatalogueEntry>(`/orgs/${org}/workflow-catalogue`, { method: "POST", body: JSON.stringify(body) }),
  /** Typed operations, grafted into the text; nothing is saved (S16-11). */
  editWorkflow: (yaml: string, operations: WorkflowOperation[]) =>
    request<WorkflowEditResult>("/workflows/edit", { method: "POST", body: JSON.stringify({ yaml, operations }) }),
  /** `signal`: a validation that typing has overtaken is cancelled rather than writing a stale verdict. */
  validateWorkflow: (yaml: string, signal?: AbortSignal) =>
    request<WorkflowValidation>("/workflows/validate", { method: "POST", body: JSON.stringify({ yaml }), signal }),
  policy: (id: string) => request<PolicyDef>(`/projects/${qualify(id)}/policy`),
  /** The organisation's policy ceiling (S27-05): a project policy may only tighten it. */
  policyCeiling: (org: string) => request<PolicyCeilingState>(`/orgs/${org}/policy-ceiling`),
  putPolicyCeiling: (org: string, ceiling: Record<string, unknown> | null) =>
    request<PolicyCeilingState>(`/orgs/${org}/policy-ceiling`, { method: "PUT", body: JSON.stringify({ ceiling }) }),

  /** The organisation's one outbound proxy (ADR 0087): the password is write-only, only `password_set` comes back. */
  outboundProxy: (org: string) => request<OutboundProxyState>(`/orgs/${org}/outbound-proxy`),
  putOutboundProxy: (org: string, body: OutboundProxyPut) =>
    request<OutboundProxyState>(`/orgs/${org}/outbound-proxy`, { method: "PUT", body: JSON.stringify(body) }),
  deleteOutboundProxy: (org: string) => request<void>(`/orgs/${org}/outbound-proxy`, { method: "DELETE" }),
  /** Fetches the fixed echo endpoint through the stored proxy; a failing proxy is a 200 with `ok: false`. */
  testOutboundProxy: (org: string) => request<OutboundProxyTest>(`/orgs/${org}/outbound-proxy/test`, { method: "POST" }),

  /** The organisation's MCP gateway (ADR 0105): the secret is write-only, only `secret_set` comes back; a PUT can answer 409 `egress_required`. */
  mcpGateway: (org: string) => request<McpGatewayState>(`/orgs/${org}/mcp-gateway`),
  putMcpGateway: (org: string, body: McpGatewayPut) =>
    request<McpGatewayState>(`/orgs/${org}/mcp-gateway`, { method: "PUT", body: JSON.stringify(body) }),
  /** A 409 `gateway_in_use` names the connectors that still go through it. */
  deleteMcpGateway: (org: string) => request<void>(`/orgs/${org}/mcp-gateway`, { method: "DELETE" }),
  /** Lists the tools of one server through the stored gateway; a failing gateway is a 200 with `ok: false`. */
  testMcpGateway: (org: string, body: McpGatewayTestRequest) =>
    request<McpGatewayTest>(`/orgs/${org}/mcp-gateway/test`, { method: "POST", body: JSON.stringify(body) }),
  /** Creates the assertion key the first time, then rotates it; the old key stays published until retired. */
  rotateMcpGatewayAssertionKey: (org: string) =>
    request<McpGatewayAssertion>(`/orgs/${org}/mcp-gateway/assertion-key`, { method: "POST" }),

  /** What a project's runs really get (ADR 0070): its policy under the organisation's ceiling, and what was clamped. */
  projectNetwork: (id: string) => request<ProjectNetwork>(`/projects/${qualify(id)}/network`),
  /** Refusals and traffic per host, for the organisation's administrators. */
  networkActivity: (org: string, params?: Record<string, string>) =>
    request<NetworkActivity>(`/orgs/${org}/network-activity${query(params)}`),

  workItems: (id: string, params?: Record<string, string>) =>
    request<WorkItemPage>(`/projects/${qualify(id)}/work-items${query(params)}`),
  /**
   * Every work item of a project, page after page, up to `cap` (S55-03). The board, the overview and
   * the inbox's count read the first page only — 50 tickets — and the rest vanished without a word.
   * `meta.has_more` stays true when the cap stopped the reading: the page must say so.
   */
  // A method, not an arrow: it reads the pages through `this.workItems`, the one a test stubs.
  allWorkItems(id: string, cap: number = WORK_ITEMS_CAP): Promise<WorkItemPage> {
    return allWorkItems((params) => this.workItems(id, params), cap);
  },
  workItem: (id: string) => request<WorkItemDto>(`/work-items/${id}`),
  /** `PATCH /work-items/{id}`: the fields that change, `null` removing one (its repository: ADR 0124). */
  updateWorkItem: (id: string, fields: Record<string, unknown>) =>
    request<WorkItemDto>(`/work-items/${id}`, { method: "PATCH", body: JSON.stringify({ fields }) }),
  /** The webhook events a project received and what the platform did with each (S42-06f), newest first. */
  receivedEvents: (id: string, params?: Record<string, string>) =>
    request<ReceivedEventPage>(`/projects/${qualify(id)}/received-events${query(params)}`),
  /** The applications that call the A2A door under their own name (ADR 0131), and their tokens. */
  machineAccounts: (id: string) => request<MachineAccount[]>(`/projects/${qualify(id)}/machine-accounts`),
  createMachineAccount: (id: string, body: MachineAccountCreate) =>
    request<MachineAccount>(`/projects/${qualify(id)}/machine-accounts`, { method: "POST", body: JSON.stringify(body) }),
  removeMachineAccount: (id: string, slug: string) =>
    request<void>(`/projects/${qualify(id)}/machine-accounts/${encodeURIComponent(slug)}`, { method: "DELETE" }),
  /** Issues a token, shown once. */
  createMachineToken: (id: string, slug: string, body: MachineTokenCreate) =>
    request<ApiTokenCreated>(`/projects/${qualify(id)}/machine-accounts/${encodeURIComponent(slug)}/tokens`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  revokeMachineToken: (id: string, slug: string, tokenId: string) =>
    request<void>(`/projects/${qualify(id)}/machine-accounts/${encodeURIComponent(slug)}/tokens/${tokenId}`, {
      method: "DELETE",
    }),
  /** The project's HTTP triggers (S56-01, ADR 0113): URL, workflow, whether a token exists. */
  triggers: (id: string) => request<Trigger[]>(`/projects/${qualify(id)}/triggers`),
  /** Mints a trigger's token and signing secret, shown once; the previous ones stop working. */
  rotateTriggerToken: (id: string, name: string) =>
    request<TriggerToken>(`/projects/${qualify(id)}/triggers/${encodeURIComponent(name)}/token`, { method: "POST" }),
  /** Fires a trigger by hand with a payload, as the person (S56-02). */
  fireTrigger: (id: string, name: string, payload: Record<string, unknown>) =>
    request<TriggerAck>(`/projects/${qualify(id)}/triggers/${encodeURIComponent(name)}/fire`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  timeline: (id: string) => request<TimelineEntry[]>(`/work-items/${id}/timeline`),
  /** A ticket's handover receipts (S25-04): who produced, who read, under which fingerprint. */
  handOffs: (id: string) => request<HandOff[]>(`/work-items/${id}/hand-offs`),
  /** Every revision of every artifact of a work item (ADR 0049). */
  artifacts: (id: string) => request<Artifact[]>(`/work-items/${id}/artifacts`),
  /** The bytes of one revision (`name@version`), digest checked by the API; a refusal is an `ApiError`. */
  artifactContent: (id: string, ref: string) => artifactContent(id, ref),
  /** The ticket's journey in ITS workflow, in one read: what the animated map draws (S22-01). */
  journey: (id: string) => request<WorkItemJourney>(`/work-items/${id}/journey`),
  decide: (id: string, body: unknown) =>
    request<unknown>(`/work-items/${id}/decisions`, { method: "POST", body: JSON.stringify(body) }),
  action: (id: string, action: string) =>
    request<unknown>(`/work-items/${id}/actions`, { method: "POST", body: JSON.stringify({ action }) }),
  /**
   * Readdresses a waiting request to a person, a group, or both (ADR 0153): an organisation
   * administrator's gesture, audited with the reason. 422 for a person outside the organisation.
   */
  reassign: (id: string, requestId: string, body: AssigneeChange) =>
    request<HumanRequest>(`/work-items/${id}/human-requests/${encodeURIComponent(requestId)}/assignee`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /**
   * Moves a work item to another active workflow of its project, at that workflow's initial state
   * (ADR 0123, S52-11). Refused (409) while a run is in progress, once the item ended and to its own
   * workflow; 422 for a workflow unknown or inactive.
   */
  reroute: (id: string, workflow: string, reason?: string) =>
    request<unknown>(`/work-items/${id}/actions`, {
      method: "POST",
      body: JSON.stringify({ action: "reroute", workflow, ...(reason ? { reason } : {}) }),
    }),
  /** Stops a work item with a reason (ADR 0142 §3): its run in flight is cancelled with the same reason. */
  stopWorkItem: (id: string, reason: string) =>
    request<unknown>(`/work-items/${id}/actions`, { method: "POST", body: JSON.stringify({ action: "stop", reason }) }),

  runs: (itemId: string) => request<Run[]>(`/work-items/${itemId}/runs`),
  run: (runId: string) => request<Run>(`/runs/${runId}`),
  /**
   * Stops one run now, with a reason (ADR 0142 §2): its work item then waits for a person. 409 when the
   * run has already ended (how it ended is in the problem); 403 without `item:control` on its project.
   */
  cancelRun: (runId: string, reason: string) =>
    request<Run>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", body: JSON.stringify({ reason } satisfies StopRequest) }),
  /** What is in flight in the organisation, newest first, then what ended in the last hour (ADR 0142 §1). */
  activity: (org: string, filter: ActivityFilter = {}) =>
    request<ActivityList>(`/orgs/${encodeURIComponent(org)}/activity${query(compact(filter))}`),
  /** The same entries as they change (SSE), with the same filter: the path `useLiveRefresh` listens to. */
  activityStreamPath: (org: string, filter: ActivityFilter = {}) =>
    `/orgs/${encodeURIComponent(org)}/activity/stream${query(compact(filter))}`,
  runEvents: (runId: string, afterSeq?: number) =>
    request<RunEventDto[]>(`/runs/${runId}/events${query(afterSeq ? { after_seq: String(afterSeq) } : undefined)}`),
  runDiff: (runId: string) => request<DiffSummary>(`/runs/${runId}/diff`),
  /** What fills the run's model context: the last call's sections and the series of totals (ADR 0141 §6). */
  runContext: (runId: string) => request<RunContext>(`/runs/${runId}/context-breakdown`),

  releases: (id: string, env?: string) => request<ReleasePage>(`/projects/${qualify(id)}/releases${query(env ? { env } : undefined)}`),
  train: (id: string, env: string) => request<TrainStatus>(`/projects/${qualify(id)}/trains/${env}`),
  departTrain: (id: string, env: string) => request<unknown>(`/projects/${qualify(id)}/trains/${env}/depart`, { method: "POST" }),
  freezeTrain: (id: string, env: string, reason: string) =>
    request<unknown>(`/projects/${qualify(id)}/trains/${env}/freeze`, { method: "POST", body: JSON.stringify({ reason }) }),
  unfreezeTrain: (id: string, env: string) =>
    request<unknown>(`/projects/${qualify(id)}/trains/${env}/unfreeze`, { method: "POST" }),
  approveRelease: (releaseId: string, note?: string) =>
    request<unknown>(`/releases/${releaseId}/approve`, { method: "POST", body: JSON.stringify({ note }) }),

  findings: (id: string, params?: Record<string, string>) =>
    request<FindingPage>(`/projects/${qualify(id)}/findings${query(params)}`),
  findingAction: (findingId: string, action: string) =>
    request<unknown>(`/findings/${findingId}/actions`, { method: "POST", body: JSON.stringify({ action }) }),

  /** `which` picks the project's memory to read (S40-11): `shared` (the default), `own` with an agent, or a named one. */
  memorySearch: (id: string, q: string, which?: MemoryChoice) =>
    request<Memory[]>(`/projects/${qualify(id)}/memory/search${query({ q, ...memoryParams(which) })}`),
  memoryPending: (id: string, which?: MemoryChoice) =>
    request<Memory[]>(`/projects/${qualify(id)}/memory/pending${query(memoryParams(which))}`),
  memoryDecide: (id: string, memoryId: string, action: "accept" | "reject", which?: MemoryChoice) =>
    request<unknown>(`/projects/${qualify(id)}/memory/pending${query(memoryParams(which))}`, {
      method: "POST",
      body: JSON.stringify({ id: memoryId, action }),
    }),
  /**
   * Asks the project's memory ingestion to read its sources again (`memory:write`). The API answers
   * 202 with no documented body: the import is scheduled, its facts arrive as the ingestion runs.
   * Without `sources`, the API's defaults (README, docs, ADRs, closed issues).
   */
  reimportMemory: (id: string, sources?: string[]) =>
    request<void>(`/projects/${qualify(id)}/memory/reimport`, {
      method: "POST",
      body: JSON.stringify(sources ? { sources } : {}),
    }),
  /** Named memories (S40-11, ADR 0075): the organisation's, global and project, with who uses each. */
  memories: (org: string, filter: { scope?: "global" | "project"; project?: string } = {}) =>
    request<MemoryStore[]>(`/orgs/${org}/memories${query(compact(filter))}`),
  /** Tickets with and without a memory over the last `weeks` (1-52): the report's verdict says whether memory helps. */
  memoryAbReport: (org: string = DEFAULT_ORG, weeks = 4) =>
    request<MemoryAbReport>(`/orgs/${org}/memory/ab-report${query({ weeks: String(weeks) })}`),
  /** Platform administrators only: the keys the model gateway minted for runs, never the secret. */
  gatewayKeys: () => request<GatewayKeyInfo[]>("/platform/gateway/keys"),
  memory: (org: string, slug: string) => request<MemoryStore>(`/orgs/${org}/memories/${slug}`),
  createGlobalMemory: (org: string, body: MemoryStoreCreate) =>
    request<MemoryStore>(`/orgs/${org}/memories`, { method: "POST", body: JSON.stringify(body) }),
  updateMemory: (org: string, slug: string, body: MemoryStorePatch) =>
    request<MemoryStore>(`/orgs/${org}/memories/${slug}`, { method: "PATCH", body: JSON.stringify(body) }),
  searchGlobalMemory: (org: string, slug: string, q: string) =>
    request<Memory[]>(`/orgs/${org}/memories/${slug}/search${query({ q })}`),
  globalMemoryPending: (org: string, slug: string) => request<Memory[]>(`/orgs/${org}/memories/${slug}/pending`),
  decideGlobalMemory: (org: string, slug: string, memoryId: string, action: "accept" | "reject") =>
    request<unknown>(`/orgs/${org}/memories/${slug}/pending`, {
      method: "POST",
      body: JSON.stringify({ id: memoryId, action }),
    }),
  /** What a run of the project may bind: `shared`, `own`, its named memories, the global ones that reach it. */
  projectMemories: (id: string) => request<MemoryStore[]>(`/projects/${qualify(id)}/memories`),
  createProjectMemory: (id: string, body: ProjectMemoryCreate) =>
    request<MemoryStore>(`/projects/${qualify(id)}/memories`, { method: "POST", body: JSON.stringify(body) }),
  updateProjectMemory: (id: string, slug: string, body: MemoryStorePatch) =>
    request<MemoryStore>(`/projects/${qualify(id)}/memories/${slug}`, { method: "PATCH", body: JSON.stringify(body) }),

  costs: (id: string, groupBy = "day", filtre: CostFilter = {}) =>
    request<CostReport>(`/projects/${qualify(id)}/costs${query({ group_by: groupBy, ...compact(filtre) })}`),
  /** The organisation's spend, in any grouping (S29-02): `since`, `until` (YYYY-MM-DD), `kind`, `agent`, `workflow` and `work_item` filter it. */
  orgCosts: (org: string, groupBy = "day", filtre: CostFilter = {}) =>
    request<CostReport>(`/orgs/${org}/costs${query({ group_by: groupBy, ...compact(filtre) })}`),
  /** The budgets that crossed 80 or 100 % (S29-04): the inbox lists the open ones. */
  budgetAlerts: (org: string, status: "open" | "acknowledged" | "all" = "open") =>
    request<BudgetAlert[]>(`/orgs/${org}/budget-alerts${query({ status })}`),
  /** The organisation's egress allowlist: rules, requests, the deployment's ceiling (S39-22, ADR 0066). */
  orgEgress: (org: string) => request<EgressOverview>(`/orgs/${org}/egress`),
  checkOrgEgress: (org: string, body: EgressCheck) =>
    request<EgressCheckResult>(`/orgs/${org}/egress/check`, { method: "POST", body: JSON.stringify(body) }),
  createOrgEgressRule: (org: string, body: EgressRuleCreate) =>
    request<EgressRule>(`/orgs/${org}/egress/rules`, { method: "POST", body: JSON.stringify(body) }),
  revokeOrgEgressRule: (org: string, id: string) =>
    request<EgressRule>(`/orgs/${org}/egress/rules/${id}`, { method: "DELETE" }),
  requestOrgEgress: (org: string, body: EgressRequestCreate) =>
    request<EgressRule>(`/orgs/${org}/egress/requests`, { method: "POST", body: JSON.stringify(body) }),
  approveOrgEgressRequest: (org: string, id: string, body: EgressDecision = {}) =>
    request<EgressRule>(`/orgs/${org}/egress/requests/${id}/approve`, { method: "POST", body: JSON.stringify(body) }),
  refuseOrgEgressRequest: (org: string, id: string, body: EgressDecision = {}) =>
    request<EgressRule>(`/orgs/${org}/egress/requests/${id}/refuse`, { method: "POST", body: JSON.stringify(body) }),
  acknowledgeBudgetAlert: (org: string, id: string) =>
    request<BudgetAlert>(`/orgs/${org}/budget-alerts/${id}/acknowledge`, { method: "POST" }),
  orgCostsCsvUrl: (org: string, groupBy = "day", filtre: CostFilter = {}) =>
    `${API_BASE}/orgs/${org}/costs.csv${query({ group_by: groupBy, ...compact(filtre) })}`,
  dora: (id: string, env = "prod") => request<DoraReport>(`/projects/${qualify(id)}/metrics/dora${query({ env })}`),
  /** Reviews by another backend than the implementer's versus by the same one; `since` is YYYY-MM-DD. */
  crossBackend: (id: string, since?: string) =>
    request<CrossBackendReport>(`/projects/${qualify(id)}/metrics/cross-backend${query(since ? { since } : undefined)}`),
  /** Direct download link: the browser opens it, the CSV arrives with its session. */
  /** Qualified like every project route: two organisations can have a project of the same name. */
  costsCsvUrl: (id: string, groupBy = "day", filtre: CostFilter = {}) =>
    `${API_BASE}/projects/${qualify(id)}/costs.csv${query({ group_by: groupBy, ...compact(filtre) })}`,
  modelMatrix: (id: string) => request<ModelMatrix>(`/projects/${qualify(id)}/models/matrix`),
  audit: (params?: { actor?: string; target_type?: string; cursor?: string; limit?: number }) => {
    const filtres = Object.entries(params ?? {}).filter(([, valeur]) => valeur !== undefined && valeur !== "");
    return request<AuditPage>(`/audit${filtres.length ? `?${new URLSearchParams(filtres.map(([k, v]) => [k, String(v)]))}` : ""}`);
  },
};

/** The fields of an `agent` actor that decide what its built-in agent runs (`GET /projects/{id}/builtin-agent`). */
export type BuiltinAgentQuery = {
  role: string;
  playbook?: string | null;
  model?: string | null;
  backend?: string | null;
  max_turns?: number | null;
  max_minutes?: number | null;
};

/** What the Activity list is narrowed to (ADR 0142 §1): a project's slug, a workflow, an agent, an assistant installation, a kind. */
export type ActivityFilter = {
  project?: string;
  workflow?: string;
  agent?: string;
  assistant?: string;
  kind?: "run" | "session" | "turn";
};

/**
 * What a spend report is narrowed to (ADR 0141 §1): a period, a kind of line, and one agent (its slug),
 * one workflow (its name) or one work item (its id), combinable. The same filter goes to the JSON
 * report and to its CSV.
 */
export type CostFilter = {
  since?: string;
  until?: string;
  kind?: "model" | "tool" | "distill";
  agent?: string;
  workflow?: string;
  work_item?: string;
};

function compact(filtre: Record<string, string | undefined>): Record<string, string> {
  return Object.fromEntries(Object.entries(filtre).filter(([, v]) => v)) as Record<string, string>;
}

/** Which of a project's memories a review reads (S40-11): absent, the shared one. */
export type MemoryChoice = { memory?: string; agent?: string };

function memoryParams(which?: MemoryChoice): Record<string, string> {
  const out: Record<string, string> = {};
  if (which?.memory && which.memory !== "shared") out.memory = which.memory;
  if (which?.memory === "own" && which.agent) out.agent = which.agent;
  return out;
}

/** How many tickets a project page reads at most: five pages of 200. */
export const WORK_ITEMS_CAP = 1000;

async function allWorkItems(
  readPage: (params: Record<string, string>) => Promise<WorkItemPage>,
  cap: number,
): Promise<WorkItemPage> {
  const items: WorkItemPage["items"] = [];
  let cursor: string | null | undefined;
  let hasMore = false;
  do {
    const params: Record<string, string> = { limit: "200" };
    if (cursor) params.cursor = cursor;
    const page = await readPage(params);
    items.push(...page.items);
    cursor = page.meta.next_cursor;
    hasMore = page.meta.has_more;
  } while (hasMore && cursor && items.length < cap);
  return { items, meta: { has_more: hasMore, next_cursor: hasMore ? (cursor ?? null) : null } };
}

function query(params?: Record<string, string>): string {
  if (!params) return "";
  const search = new URLSearchParams(params).toString();
  return search ? `?${search}` : "";
}

export type { DiffSummary, RunEventDto, TimelineEntry } from "./types";
