// SPDX-License-Identifier: Apache-2.0
/**
 * Client de l'API Choregos, typé par les contrats générés.
 *
 * Deux modes : `live` (l'API réelle, relayée par Next) et `mock` (fixtures locales,
 * pour que le front avance sans l'API — `NEXT_PUBLIC_API_MODE=mock`).
 */
import type {
  Action,
  Agent,
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
  GitHubAppStatus,
  GitHubInstallLink,
  GitHubInstallLinkRequest,
  GitHubRepositoryPage,
  HandOff,
  Membership,
  MembershipUpsert,
  Org,
  OrgCreate,
  OrgUpdate,
  ProjectModels,
  TemplateSummary,
  WorkItemCreate,
  DiffSummary,
  RunEventDto,
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
  DoraReport,
  Edition,
  Integrations,
  Procedure,
  ProcedureSummary,
  FindingPage,
  MeDto,
  Memory,
  MemoryStore,
  MemoryStoreCreate,
  MemoryStorePatch,
  ProjectMemoryCreate,
  ModelMatrix,
  PolicyDef,
  PolicyCeilingState,
  OutboundProxyPut,
  OutboundProxyState,
  OutboundProxyTest,
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
  WorkflowSummary,
  WorkflowTemplate,
  WorkflowValidation,
  WorkItemDto,
  WorkItemJourney,
  WorkItemPage,
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
/** Un identifiant de projet tel que l'API l'attend : un UUID tel quel, sinon `org:slug`. */
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

/** Sur 401 hors de la page de connexion : on y va, en gardant d'où l'on vient. */
function versLaConnexion(): void {
  if (typeof window === "undefined" || window.location.pathname.startsWith("/login")) return;
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  // Une navigation complète, volontairement : la session est morte, l'état React aussi.
  window.location.assign(new URL(`/login?next=${next}`, window.location.origin).toString());
}

/**
 * Un 401 `step_up_required` n'est pas une session morte : le geste demande une authentification
 * RÉCENTE (une décision d'action, ADR 0030). On repasse par l'IdP avec `reauth=1`, et l'on revient
 * ici — envoyer vers `/login` faisait boucler, puisque la session, elle, est vivante.
 */
function demandeUneReauthentification(problem: { errors?: unknown[] }): boolean {
  const premier = (problem.errors ?? [])[0] as { error?: string } | undefined;
  return premier?.error === "step_up_required";
}

function versLaReauthentification(): void {
  if (typeof window === "undefined") return;
  const ici = window.location.pathname + window.location.search;
  window.location.assign(new URL(api.loginUrl(ici, undefined, true), window.location.origin).toString());
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (IS_MOCK) {
    // Les fixtures ne partent dans le bundle que si le mode démo est demandé : importées
    // statiquement, 460 lignes de fausses données partaient chez chaque utilisateur.
    const { mockApi } = await import("@/mocks/data");
    return mockApi<T>(path, init);
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    // A file upload (`FormData`) sets its own type, with its multipart boundary.
    // A raw file (`Blob`) carries the content type its caller gives it (`sectionUpload`).
    headers:
      init.body instanceof FormData || init.body instanceof Blob
        ? { ...(init.headers ?? {}) }
        : { "Content-Type": "application/json", ...(init.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    const problem = await problemOf(response);
    if (response.status === 401) {
      if (demandeUneReauthentification(problem)) versLaReauthentification();
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

export const api = {
  me: () => request<MeDto>("/me"),
  /** L'édition qui tourne (ADR 0024) : la console montre ce qu'elle s'autorise. */
  edition: () => request<Edition>("/edition"),
  /** Où brancher un client MCP (ADR 0030) : l'URL de la porte, pour la page Integrations. */
  integrations: () => request<Integrations>("/integrations"),
  /** The setup procedures (ADR 0067); `for` answers the one best for a target, a preset before the generic one. */
  listProcedures: (query: { for?: string; card_host?: string; preset?: string } = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) if (value) params.set(key, value);
    const q = params.toString();
    return request<ProcedureSummary[]>(`/procedures${q ? `?${q}` : ""}`);
  },
  getProcedure: (id: string) => request<Procedure>(`/procedures/${encodeURIComponent(id)}`),
  /** L'URL qui ouvre la session : l'API redirige vers l'IdP (ou, en dev, ouvre directement). */
  loginUrl: (next?: string, as?: string, reauth = false) => {
    const params = new URLSearchParams();
    if (next) params.set("redirect_to", next);
    if (as) params.set("as", as);
    if (reauth) params.set("reauth", "1");
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
  /** Retire une appartenance — à l'organisation, ou au seul projet nommé. */
  removeMember: (org: string, userId: string, project?: string | null) =>
    request<void>(`/orgs/${org}/members/${encodeURIComponent(userId)}${project ? `?project=${encodeURIComponent(project)}` : ""}`, {
      method: "DELETE",
    }),
  /** Les sections d'administration que les greffons déclarent, filtrées par l'API (ADR 0032). */
  adminSections: (org: string) => request<AdminSection[]>(`/ui/admin-sections?org=${encodeURIComponent(org)}`),
  /**
   * Un chemin qu'une section déclare (relatif à `/api/v1`, `{org}` et `{id}` déjà remplis). La
   * console n'appelle que les chemins que l'API lui a rendus — et l'API a vérifié au démarrage
   * qu'une route les sert.
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
  /** Le registre d'agents de l'organisation (ADR 0033) : une version ne se modifie jamais. */
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
  /** Le catalogue de la plateforme (ADR 0040) : ce qu'il propose, ce qui en est installé. */
  agentCatalogue: (org: string) => request<AgentCatalogueEntry[]>(`/orgs/${org}/agent-catalogue`),
  installCatalogueAgent: (org: string, slug: string, upgrade = false) =>
    request<Agent>(`/orgs/${org}/agent-catalogue/${slug}/install`, {
      method: "POST",
      body: JSON.stringify({ upgrade }),
    }),
  /** Un client en un clic : son agent externe, un jeton `mcp:*` rendu une fois, ou son client OAuth. */
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
  /** La bibliothèque de skills : un dossier, importé en zip, versionné, jamais réécrit. */
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
  approveSkillVersion: (org: string, slug: string, version: number) =>
    request<SkillVersion>(`/orgs/${org}/skills/${slug}/versions/${version}/approve`, { method: "POST" }),

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
      evenements: number;
      refusals: number;
      tool_cost_eur?: number;
      acces: { nature: string; target: string; requests: number; refusals: number; motifs?: string[] }[];
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
  /** Les actions gouvernées (ADR 0035) : la boîte des décisions, et chaque action avec son journal. */
  orgActions: (org: string, status?: string) =>
    request<Action[]>(`/orgs/${org}/actions${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  projectActions: (id: string) => request<Action[]>(`/projects/${qualify(id)}/actions`),
  projectAction: (id: string, actionId: string) => request<Action>(`/projects/${qualify(id)}/actions/${actionId}`),
  /** Une approbation exige une session récente : un 401 `step_up_required` repasse par l'IdP. */
  decideAction: (id: string, actionId: string, body: { decision: "approve" | "reject"; reason?: string }) =>
    request<Action>(`/projects/${qualify(id)}/actions/${actionId}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Les connecteurs de l'organisation et la politique de chaque opération (ADR 0034). */
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
  /** The connector types of the registry, by family, with the caller's rights (ADR 0074). */
  connectorCatalogue: (org: string) => request<ConnectorCatalogue>(`/orgs/${org}/connector-catalogue`),
  /** The organisation's connectors as one project sees them: on or off, and may the caller switch. */
  projectOrgConnectors: (id: string) => request<ProjectOrgConnector[]>(`/projects/${qualify(id)}/org-connectors`),
  enableOrgConnector: (id: string, name: string) =>
    request<ProjectOrgConnector>(`/projects/${qualify(id)}/org-connectors/${name}/enablement`, { method: "PUT" }),
  disableOrgConnector: (id: string, name: string) =>
    request<void>(`/projects/${qualify(id)}/org-connectors/${name}/enablement`, { method: "DELETE" }),
  /** Un serveur MCP dit ses outils ; un nouveau naît fermé, un schéma qui dérive le referme. */
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
  /** Ce que les workflows du projet exigent de ses connecteurs, et pourquoi (ADR 0034). */
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

  workflow: (id: string) => request<WorkflowDef>(`/projects/${qualify(id)}/workflow`),
  putWorkflow: (id: string, yaml: string) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflow`, { method: "PUT", body: JSON.stringify({ yaml }) }),
  // Plusieurs workflows par projet, chacun par son nom (ADR 0031).
  workflows: (id: string) => request<WorkflowSummary[]>(`/projects/${qualify(id)}/workflows`),
  workflowNamed: (id: string, name: string) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}`),
  /** Publie la version suivante ; `baseVersion` est celle qu'on a lue — périmée, l'API répond 409. */
  putWorkflowNamed: (id: string, name: string, yaml: string, baseVersion?: number) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ yaml, base_version: baseVersion ?? null }),
    }),
  /** Crée un workflow : un nom déjà pris, actif ou non, répond 409 et rien n'est publié (#336). */
  createWorkflow: (id: string, name: string, yaml: string) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ yaml, create_only: true }),
    }),
  workflowVersions: (id: string, name: string) =>
    request<WorkflowDef[]>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}/versions`),
  /** Republie une version passée comme la suivante : rien n'est réécrit, l'historique s'allonge. */
  restoreWorkflowVersion: (id: string, name: string, version: number) =>
    request<WorkflowDef>(
      `/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}/versions/${version}/restore`,
      { method: "POST" },
    ),
  workflowRouting: (id: string) => request<WorkflowRouting>(`/projects/${qualify(id)}/workflow-routing`),
  /** Le défaut et les règles, d'un bloc : l'API refuse un workflow inconnu ou inactif (422). */
  putWorkflowRouting: (id: string, routing: WorkflowRouting) =>
    request<WorkflowRouting>(`/projects/${qualify(id)}/workflow-routing`, {
      method: "PUT",
      body: JSON.stringify(routing),
    }),
  /** Plus de ticket neuf ; l'API refuse le défaut et la cible d'une règle (409). */
  deactivateWorkflow: (id: string, name: string) =>
    request<void>(`/projects/${qualify(id)}/workflows/${encodeURIComponent(name)}/deactivate`, { method: "POST" }),
  workflowTemplates: () => request<WorkflowTemplate[]>("/workflows/templates"),
  /** Des opérations typées, greffées dans le texte ; rien n'est enregistré (S16-11). */
  editWorkflow: (yaml: string, operations: WorkflowOperation[]) =>
    request<WorkflowEditResult>("/workflows/edit", { method: "POST", body: JSON.stringify({ yaml, operations }) }),
  /** `signal` : une validation que la frappe a dépassée s'annule plutôt que d'écrire un verdict périmé. */
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

  /** What a project's runs really get (ADR 0070): its policy under the organisation's ceiling, and what was clamped. */
  projectNetwork: (id: string) => request<ProjectNetwork>(`/projects/${qualify(id)}/network`),
  /** Refusals and traffic per host, for the organisation's administrators. */
  networkActivity: (org: string, params?: Record<string, string>) =>
    request<NetworkActivity>(`/orgs/${org}/network-activity${query(params)}`),

  workItems: (id: string, params?: Record<string, string>) =>
    request<WorkItemPage>(`/projects/${qualify(id)}/work-items${query(params)}`),
  workItem: (id: string) => request<WorkItemDto>(`/work-items/${id}`),
  timeline: (id: string) => request<TimelineEntry[]>(`/work-items/${id}/timeline`),
  /** Les reçus de passage d'un ticket (S25-04) : qui a produit, qui a lu, sous quelle empreinte. */
  handOffs: (id: string) => request<HandOff[]>(`/work-items/${id}/hand-offs`),
  /** Every revision of every artifact of a work item (ADR 0049). */
  artifacts: (id: string) => request<Artifact[]>(`/work-items/${id}/artifacts`),
  /** The bytes of one revision (`name@version`), digest checked by the API; a refusal is an `ApiError`. */
  artifactContent: (id: string, ref: string) => artifactContent(id, ref),
  /** Le parcours du ticket dans SON workflow, en une lecture : ce que la carte animée dessine (S22-01). */
  journey: (id: string) => request<WorkItemJourney>(`/work-items/${id}/journey`),
  decide: (id: string, body: unknown) =>
    request<unknown>(`/work-items/${id}/decisions`, { method: "POST", body: JSON.stringify(body) }),
  action: (id: string, action: string) =>
    request<unknown>(`/work-items/${id}/actions`, { method: "POST", body: JSON.stringify({ action }) }),

  runs: (itemId: string) => request<Run[]>(`/work-items/${itemId}/runs`),
  run: (runId: string) => request<Run>(`/runs/${runId}`),
  runEvents: (runId: string, afterSeq?: number) =>
    request<RunEventDto[]>(`/runs/${runId}/events${query(afterSeq ? { after_seq: String(afterSeq) } : undefined)}`),
  runDiff: (runId: string) => request<DiffSummary>(`/runs/${runId}/diff`),

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
  /** Named memories (S40-11, ADR 0075): the organisation's, global and project, with who uses each. */
  memories: (org: string, filter: { scope?: "global" | "project"; project?: string } = {}) =>
    request<MemoryStore[]>(`/orgs/${org}/memories${query(compact(filter))}`),
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
  /** The organisation's spend, in any grouping (S29-02): `since`, `until` (YYYY-MM-DD) and `kind` filter it. */
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
  /** Lien de téléchargement direct : le navigateur l'ouvre, le CSV arrive avec sa session. */
  /** Qualifié comme toute route de projet : deux organisations peuvent avoir un projet de même nom. */
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

export type CostFilter = { since?: string; until?: string; kind?: "model" | "tool" | "distill" };

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

function query(params?: Record<string, string>): string {
  if (!params) return "";
  const search = new URLSearchParams(params).toString();
  return search ? `?${search}` : "";
}

export type { DiffSummary, RunEventDto, TimelineEntry } from "./types";
