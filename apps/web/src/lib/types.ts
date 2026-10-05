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
