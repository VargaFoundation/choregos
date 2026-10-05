// SPDX-License-Identifier: Apache-2.0
/**
 * Types de l'API, réexportés depuis les contrats générés (`make contracts`).
 * Le front ne redéfinit jamais un type de l'API : il en dépend.
 */
export type {
  AgentBackendInfo,
  ApiToken,
  ApiTokenCreate,
  ApiTokenCreated,
  ArtifactRef,
  AuditPage,
  ConnectorType,
  ExecutorInfo,
  Membership,
  MembershipUpsert,
  Org,
  OrgCreate,
  ProjectModels,
  TemplateSummary,
  WorkItemCreate,
  Connector as ConnectorDto,
  ConnectorTestResult,
  CostReport,
  DiffSummary,
  DoraMetric,
  DoraReport,
  FindingPage,
  FindingRecord,
  GatewayModel,
  HumanRequest,
  Me as MeDto,
  Memory,
  ModelMatrix,
  PolicyDef,
  Project as ProjectDto,
  ProjectPage,
  ProvisionStatus,
  Release,
  ReleasePage,
  Run,
  RunEvent as RunEventDto,
  RunSummary,
  TimelineEntry,
  TrainStatus,
  WorkflowDef,
  WorkflowGraph,
  WorkflowValidation,
  WorkItem as WorkItemDto,
  WorkItemPage,
} from "@choregos/contracts/api";

import type { Operations } from "@choregos/contracts/api";

export type { Integrations } from "@choregos/contracts/api";

/** L'édition qui tourne et ce qu'elle s'autorise (ADR 0024) : la réponse de `GET /edition`. */
export type Edition = Operations["edition"]["response"];

/**
 * Une proposition d'action de l'ontologie : le greffon `choregos-ontology` la sert hors du contrat du
 * cœur, comme `runAccess` — d'où un type écrit ici, tenu par la réponse de `actions.describe`.
 */
export interface Proposal {
  proposal: string;
  action_type: string;
  status: string;
  target: string[];
  params: Record<string, unknown>;
  justification: string;
  proposed_by: { kind: string; id: string; via?: string; run_id?: string };
  approval: {
    mode?: string;
    approvers?: Array<{ role: string; min?: number }>;
    step_up_minutes?: number | null;
    separation_of_duties?: boolean;
  };
  decisions: Array<{ by: string; decision: string; reason?: string | null; at: string; auth_time?: number | null }>;
  effects: Array<Record<string, unknown>>;
  evidence: Array<Record<string, unknown>>;
  created_at: string | null;
  finished_at: string | null;
}

