/**
 * Client de l'API Choregos, typé par les contrats générés.
 *
 * Deux modes : `live` (l'API réelle, relayée par Next) et `mock` (fixtures locales,
 * pour que le front avance sans l'API — `NEXT_PUBLIC_API_MODE=mock`).
 */
import type {
  AgentBackendInfo,
  ApiToken,
  ApiTokenCreate,
  ApiTokenCreated,
  ArtifactRef,
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
  WorkflowValidation,
  WorkItemDto,
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
    super(problem.detail ?? problem.title ?? `Erreur ${status}`);
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
    let problem: { title?: string; detail?: string } = {};
    try {
      problem = await response.json();
    } catch {
      problem = { title: response.statusText };
    }
    if (response.status === 401) versLaConnexion();
    throw new ApiError(response.status, problem);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  me: () => request<MeDto>("/me"),
  /** L'URL qui ouvre la session : l'API redirige vers l'IdP (ou, en dev, ouvre directement). */
  loginUrl: (next?: string, as?: string) => {
    const params = new URLSearchParams();
    if (next) params.set("redirect_to", next);
    if (as) params.set("as", as);
    const q = params.toString();
    return `${API_BASE}/auth/login${q ? `?${q}` : ""}`;
  },
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  orgs: () => request<Org[]>("/orgs"),
  createOrg: (body: OrgCreate) => request<Org>("/orgs", { method: "POST", body: JSON.stringify(body) }),
  members: (org: string) => request<Membership[]>(`/orgs/${org}/members`),
  addMember: (org: string, body: MembershipUpsert) =>
    request<Membership>(`/orgs/${org}/members`, { method: "POST", body: JSON.stringify(body) }),
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
  putConnector: (id: string, kind: string, body: unknown) =>
    request<ConnectorDto>(`/projects/${qualify(id)}/connectors/${kind}`, { method: "PUT", body: JSON.stringify(body) }),
  testConnector: (id: string, kind: string) =>
    request<ConnectorTestResult>(`/projects/${qualify(id)}/connectors/${kind}/test`, { method: "POST" }),

  workflow: (id: string) => request<WorkflowDef>(`/projects/${qualify(id)}/workflow`),
  putWorkflow: (id: string, yaml: string) =>
    request<WorkflowDef>(`/projects/${qualify(id)}/workflow`, { method: "PUT", body: JSON.stringify({ yaml }) }),
  validateWorkflow: (yaml: string) =>
    request<WorkflowValidation>("/workflows/validate", { method: "POST", body: JSON.stringify({ yaml }) }),
  policy: (id: string) => request<PolicyDef>(`/projects/${qualify(id)}/policy`),

  workItems: (id: string, params?: Record<string, string>) =>
    request<WorkItemPage>(`/projects/${qualify(id)}/work-items${query(params)}`),
  workItem: (id: string) => request<WorkItemDto>(`/work-items/${id}`),
  timeline: (id: string) => request<TimelineEntry[]>(`/work-items/${id}/timeline`),
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
  costsCsvUrl: (id: string, groupBy = "day") =>
    `${API_BASE}/projects/${id}/costs.csv${query({ group_by: groupBy })}`,
  modelMatrix: (id: string) => request<ModelMatrix>(`/projects/${qualify(id)}/models/matrix`),
  audit: () => request<AuditPage>("/audit"),
};

function query(params?: Record<string, string>): string {
  if (!params) return "";
  const search = new URLSearchParams(params).toString();
  return search ? `?${search}` : "";
}

export type { DiffSummary, RunEventDto, TimelineEntry } from "./types";
