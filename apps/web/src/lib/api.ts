/**
 * Client de l'API Choregos, typé par les contrats générés.
 *
 * Deux modes : `live` (l'API réelle, relayée par Next) et `mock` (fixtures locales,
 * pour que le front avance sans l'API — `NEXT_PUBLIC_API_MODE=mock`).
 */
import type {
  AuditPage,
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
import { mockApi } from "@/mocks/data";

export const API_BASE = "/api/v1";
export const IS_MOCK = process.env.NEXT_PUBLIC_API_MODE === "mock";
export const DEFAULT_ORG = process.env.NEXT_PUBLIC_DEFAULT_ORG ?? "varga";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: { title?: string; detail?: string; errors?: unknown[] },
  ) {
    super(problem.detail ?? problem.title ?? `Erreur ${status}`);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (IS_MOCK) return mockApi<T>(path, init);
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
    throw new ApiError(response.status, problem);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  me: () => request<MeDto>("/me"),

  projects: (org: string = DEFAULT_ORG) => request<ProjectPage>(`/orgs/${org}/projects`),
  project: (id: string) => request<ProjectDto>(`/projects/${id}`),
  createProject: (org: string, body: unknown) =>
    request<ProjectDto>(`/orgs/${org}/projects`, { method: "POST", body: JSON.stringify(body) }),
  provision: (id: string) => request<ProvisionStatus>(`/projects/${id}/provision`, { method: "POST", body: "{}" }),
  provisionStatus: (id: string) => request<ProvisionStatus>(`/projects/${id}/provision`),

  connectors: (id: string) => request<ConnectorDto[]>(`/projects/${id}/connectors`),
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
    }>(`/projects/${id}/tools`),
  putConnector: (id: string, kind: string, body: unknown) =>
    request<ConnectorDto>(`/projects/${id}/connectors/${kind}`, { method: "PUT", body: JSON.stringify(body) }),
  testConnector: (id: string, kind: string) =>
    request<ConnectorTestResult>(`/projects/${id}/connectors/${kind}/test`, { method: "POST" }),

  workflow: (id: string) => request<WorkflowDef>(`/projects/${id}/workflow`),
  putWorkflow: (id: string, yaml: string) =>
    request<WorkflowDef>(`/projects/${id}/workflow`, { method: "PUT", body: JSON.stringify({ yaml }) }),
  validateWorkflow: (yaml: string) =>
    request<WorkflowValidation>("/workflows/validate", { method: "POST", body: JSON.stringify({ yaml }) }),
  policy: (id: string) => request<PolicyDef>(`/projects/${id}/policy`),

  workItems: (id: string, params?: Record<string, string>) =>
    request<WorkItemPage>(`/projects/${id}/work-items${query(params)}`),
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

  releases: (id: string, env?: string) => request<ReleasePage>(`/projects/${id}/releases${query(env ? { env } : undefined)}`),
  train: (id: string, env: string) => request<TrainStatus>(`/projects/${id}/trains/${env}`),
  departTrain: (id: string, env: string) => request<unknown>(`/projects/${id}/trains/${env}/depart`, { method: "POST" }),
  freezeTrain: (id: string, env: string, reason: string) =>
    request<unknown>(`/projects/${id}/trains/${env}/freeze`, { method: "POST", body: JSON.stringify({ reason }) }),
  unfreezeTrain: (id: string, env: string) =>
    request<unknown>(`/projects/${id}/trains/${env}/unfreeze`, { method: "POST" }),
  approveRelease: (releaseId: string, note?: string) =>
    request<unknown>(`/releases/${releaseId}/approve`, { method: "POST", body: JSON.stringify({ note }) }),

  findings: (id: string, params?: Record<string, string>) =>
    request<FindingPage>(`/projects/${id}/findings${query(params)}`),
  findingAction: (findingId: string, action: string) =>
    request<unknown>(`/findings/${findingId}/actions`, { method: "POST", body: JSON.stringify({ action }) }),

  memorySearch: (id: string, q: string) => request<Memory[]>(`/projects/${id}/memory/search${query({ q })}`),
  memoryPending: (id: string) => request<Memory[]>(`/projects/${id}/memory/pending`),
  memoryDecide: (id: string, memoryId: string, action: "accept" | "reject") =>
    request<unknown>(`/projects/${id}/memory/pending`, {
      method: "POST",
      body: JSON.stringify({ id: memoryId, action }),
    }),

  costs: (id: string, groupBy = "day") => request<CostReport>(`/projects/${id}/costs${query({ group_by: groupBy })}`),
  dora: (id: string, env = "prod") => request<DoraReport>(`/projects/${id}/metrics/dora${query({ env })}`),
  /** Lien de téléchargement direct : le navigateur l'ouvre, le CSV arrive avec sa session. */
  costsCsvUrl: (id: string, groupBy = "day") =>
    `${API_BASE}/projects/${id}/costs.csv${query({ group_by: groupBy })}`,
  modelMatrix: (id: string) => request<ModelMatrix>(`/projects/${id}/models/matrix`),
  audit: () => request<AuditPage>("/audit"),
};

function query(params?: Record<string, string>): string {
  if (!params) return "";
  const search = new URLSearchParams(params).toString();
  return search ? `?${search}` : "";
}

export type { DiffSummary, RunEventDto, TimelineEntry } from "./types";
