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
  OrgConnector,
  OrgConnectorCreate,
  ProjectOperation,
  ProjectRequirement,
  AgentCatalogueConnection,
  AgentCatalogueEntry,
  AgentCreate,
  AgentCredential,
  AgentMetrics,
  AgentPatch,
  AgentSpec,
  AgentVersion,
  ProjectAgent,
  ProjectAgentPut,
  Skill,
  SkillVersion,
  AgentBackendInfo,
  ApiToken,
  ApiTokenCreate,
  ApiTokenCreated,
  ArtifactRef,
  AdminSection,
  AuditPage,
  ConnectorType,
  ExecutorInfo,
  GatewayModel,
  Membership,
  MembershipUpsert,
  Org,
  OrgCreate,
  ProjectModels,
  TemplateSummary,
  WorkItemCreate,
  DiffSummary,
  RunEventDto,
  TimelineEntry,
  ConnectorDto,
  ConnectorTestResult,
  CostReport,
  DoraReport,
  Edition,
  Integrations,
  FindingPage,
  MeDto,
  Memory,
  ModelMatrix,
  PolicyDef,
  ProjectDto,
  ProjectPage,
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
/** L'organisation de départ, avant que la session n'en dise plus (mode démo, premier rendu). */
export const DEFAULT_ORG = process.env.NEXT_PUBLIC_DEFAULT_ORG ?? "varga";

// ─── l'organisation courante ───
// Le front n'avait qu'une organisation, codée en dur (`varga`). Les projets sont adressés à
// l'API par `org:slug` — un slug seul est ambigu dès que deux organisations partagent un
// nom de projet, et l'API le refuse. La session pose l'organisation ici ; les pages passent
// des slugs, `qualify` fait le reste.
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

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: { title?: string; detail?: string; errors?: unknown[] },
  ) {
    super(problem.detail ?? problem.title ?? `request failed (HTTP ${status})`);
    this.name = "ApiError";
  }
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
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    let problem: { title?: string; detail?: string; errors?: unknown[] } = {};
    try {
      problem = await response.json();
    } catch {
      problem = { title: response.statusText };
    }
    if (response.status === 401) {
      if (demandeUneReauthentification(problem as { errors?: unknown[] })) versLaReauthentification();
      else versLaConnexion();
    }
    throw new ApiError(response.status, problem);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  me: () => request<MeDto>("/me"),
  /** L'édition qui tourne (ADR 0024) : la console montre ce qu'elle s'autorise. */
  edition: () => request<Edition>("/edition"),
  /** Où brancher un client MCP (ADR 0030) : l'URL de la porte, pour la page Integrations. */
  integrations: () => request<Integrations>("/integrations"),
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
  members: (org: string) => request<Membership[]>(`/orgs/${org}/members`),
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
  addMember: (org: string, body: MembershipUpsert) =>
    request<Membership>(`/orgs/${org}/members`, { method: "POST", body: JSON.stringify(body) }),
  /** Le registre d'agents de l'organisation (ADR 0033) : une version ne se modifie jamais. */
  agents: (org: string) => request<Agent[]>(`/orgs/${org}/agents`),
  agent: (org: string, slug: string) => request<Agent>(`/orgs/${org}/agents/${slug}`),
  createAgent: (org: string, body: AgentCreate) =>
    request<Agent>(`/orgs/${org}/agents`, { method: "POST", body: JSON.stringify(body) }),
  updateAgent: (org: string, slug: string, body: AgentPatch) =>
    request<Agent>(`/orgs/${org}/agents/${slug}`, { method: "PATCH", body: JSON.stringify(body) }),
  publishAgentVersion: (org: string, slug: string, spec: AgentSpec) =>
    request<AgentVersion>(`/orgs/${org}/agents/${slug}/versions`, { method: "POST", body: JSON.stringify(spec) }),
  agentMetrics: (org: string, slug: string, days = 30) =>
    request<AgentMetrics>(`/orgs/${org}/agents/${slug}/metrics?days=${days}`),
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
  skill: (org: string, slug: string) => request<Skill>(`/orgs/${org}/skills/${slug}`),
  skillVersion: (org: string, slug: string, version: number) =>
    request<SkillVersion>(`/orgs/${org}/skills/${slug}/versions/${version}`),
  importSkill: (org: string, archive: Blob) =>
    request<Skill>(`/orgs/${org}/skills/import`, {
      method: "POST",
      body: archive,
      headers: { "Content-Type": "application/zip" },
    }),
  myTokens: () => request<ApiToken[]>("/me/tokens"),
  createToken: (body: ApiTokenCreate) =>
    request<ApiTokenCreated>("/me/tokens", { method: "POST", body: JSON.stringify(body) }),
  revokeToken: (id: string) => request<void>(`/me/tokens/${id}`, { method: "DELETE" }),
  templates: () => request<TemplateSummary[]>("/templates"),
  connectorTypes: () => request<ConnectorType[]>("/connectors/types"),
  platformModels: () => request<GatewayModel[]>("/platform/models"),
  backends: () => request<AgentBackendInfo[]>("/platform/backends"),
  executors: () => request<ExecutorInfo[]>("/platform/executors"),
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

  projects: (org: string = DEFAULT_ORG) => request<ProjectPage>(`/orgs/${org}/projects`),
  project: (id: string) => request<ProjectDto>(`/projects/${qualify(id)}`),
  createProject: (org: string, body: unknown) =>
    request<ProjectDto>(`/orgs/${org}/projects`, { method: "POST", body: JSON.stringify(body) }),
  provision: (id: string) => request<ProvisionStatus>(`/projects/${qualify(id)}/provision`, { method: "POST", body: "{}" }),
  provisionStatus: (id: string) => request<ProvisionStatus>(`/projects/${qualify(id)}/provision`),

  connectors: (id: string) => request<ConnectorDto[]>(`/projects/${qualify(id)}/connectors`),
  runAccess: (id: string) =>
    request<{
      evenements: number;
      refus: number;
      cout_outils_eur?: number;
      acces: { nature: string; cible: string; demandes: number; refus: number; motifs?: string[] }[];
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
  updateConnectorOperation: (org: string, name: string, operation: string, body: OperationPatch) =>
    request<ConnectorOperation>(`/orgs/${org}/connectors/${name}/operations/${encodeURIComponent(operation)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
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
  testConnector: (id: string, kind: string) =>
    request<ConnectorTestResult>(`/projects/${qualify(id)}/connectors/${kind}/test`, { method: "POST" }),

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

  workItems: (id: string, params?: Record<string, string>) =>
    request<WorkItemPage>(`/projects/${qualify(id)}/work-items${query(params)}`),
  workItem: (id: string) => request<WorkItemDto>(`/work-items/${id}`),
  timeline: (id: string) => request<TimelineEntry[]>(`/work-items/${id}/timeline`),
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

  memorySearch: (id: string, q: string) => request<Memory[]>(`/projects/${qualify(id)}/memory/search${query({ q })}`),
  memoryPending: (id: string) => request<Memory[]>(`/projects/${qualify(id)}/memory/pending`),
  memoryDecide: (id: string, memoryId: string, action: "accept" | "reject") =>
    request<unknown>(`/projects/${qualify(id)}/memory/pending`, {
      method: "POST",
      body: JSON.stringify({ id: memoryId, action }),
    }),

  costs: (id: string, groupBy = "day") => request<CostReport>(`/projects/${qualify(id)}/costs${query({ group_by: groupBy })}`),
  dora: (id: string, env = "prod") => request<DoraReport>(`/projects/${qualify(id)}/metrics/dora${query({ env })}`),
  /** Lien de téléchargement direct : le navigateur l'ouvre, le CSV arrive avec sa session. */
  /** Qualifié comme toute route de projet : deux organisations peuvent avoir un projet de même nom. */
  costsCsvUrl: (id: string, groupBy = "day") =>
    `${API_BASE}/projects/${qualify(id)}/costs.csv${query({ group_by: groupBy })}`,
  modelMatrix: (id: string) => request<ModelMatrix>(`/projects/${qualify(id)}/models/matrix`),
  audit: (params?: { actor?: string; target_type?: string; cursor?: string; limit?: number }) => {
    const filtres = Object.entries(params ?? {}).filter(([, valeur]) => valeur !== undefined && valeur !== "");
    return request<AuditPage>(`/audit${filtres.length ? `?${new URLSearchParams(filtres.map(([k, v]) => [k, String(v)]))}` : ""}`);
  },
};

function query(params?: Record<string, string>): string {
  if (!params) return "";
  const search = new URLSearchParams(params).toString();
  return search ? `?${search}` : "";
}

export type { DiffSummary, RunEventDto, TimelineEntry } from "./types";
