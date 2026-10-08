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
  ProcessStep,
  WorkflowDef,
  WorkflowEditResult,
  WorkflowGraph,
  WorkflowOperation,
  WorkflowRouting,
  WorkflowSummary,
  WorkflowTemplate,
  WorkflowValidation,
  WorkItem as WorkItemDto,
  WorkItemPage,
  WorkItemJourney,
  JourneyMove,
  JourneyStep,
} from "@choregos/contracts/api";

import type { Operations } from "@choregos/contracts/api";

export type {
  Action,
  ActionEffect,
  ConnectorDiscovery,
  ConnectorOperation,
  Integrations,
  OperationPatch,
  OrgConnector,
  OrgConnectorCreate,
  ProjectOperation,
  ProjectRequirement,
} from "@choregos/contracts/api";

/** Le registre d'agents et la bibliothèque de skills (ADR 0033). */
export type {
  Agent,
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
} from "@choregos/contracts/api";

/** Une section d'administration déclarée par un greffon, et ses blocs (ADR 0032). */
export type {
  UiManifest as AdminSection,
  UiManifestAction as AdminAction,
  UiManifestForm as AdminForm,
  UiManifestSecretOnce as AdminSecretOnce,
  UiManifestTable as AdminTable,
} from "@choregos/contracts/schemas";

/** L'édition qui tourne et ce qu'elle s'autorise (ADR 0024) : la réponse de `GET /edition`. */
export type Edition = Operations["edition"]["response"];
