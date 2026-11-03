# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `schemas.projects`, ADR 0098
"""The former name of `choregos_api.schemas.projects`: every name still resolves."""

from __future__ import annotations

from choregos_core.compat import replace_module

from . import projects as _current
from .projects import (
    ConnectorCheck,
    ConnectorDiscovery,
    ConnectorDto,
    ConnectorSecretState,
    ConnectorTestResult,
    ConnectorType,
    ConnectorUpsert,
    OperationDto,
    OperationPatch,
    OperationPolicy,
    OrgConnectorCreate,
    OrgConnectorDto,
    OrgConnectorServer,
    OrgConnectorUpdate,
    ProjectCreate,
    ProjectDto,
    ProjectOperationDto,
    ProjectOperationPut,
    ProjectOrgConnectorDto,
    ProjectPage,
    ProjectRequirement,
    ProjectStats,
    ProjectUpdate,
    ProvisionRequest,
    ProvisionStatus,
    ProvisionStep,
    SecretsRewrap,
    SecretValue,
    TemplateDetail,
    TemplateSummary,
    TemplateUpsert,
)

__all__ = [
    "ConnectorCheck",
    "ConnectorDiscovery",
    "ConnectorDto",
    "ConnectorSecretState",
    "ConnectorTestResult",
    "ConnectorType",
    "ConnectorUpsert",
    "OperationDto",
    "OperationPatch",
    "OperationPolicy",
    "OrgConnectorCreate",
    "OrgConnectorDto",
    "OrgConnectorServer",
    "OrgConnectorUpdate",
    "ProjectCreate",
    "ProjectDto",
    "ProjectOperationDto",
    "ProjectOperationPut",
    "ProjectOrgConnectorDto",
    "ProjectPage",
    "ProjectRequirement",
    "ProjectStats",
    "ProjectUpdate",
    "ProvisionRequest",
    "ProvisionStatus",
    "ProvisionStep",
    "SecretValue",
    "SecretsRewrap",
    "TemplateDetail",
    "TemplateSummary",
    "TemplateUpsert",
]

replace_module(__name__, _current)
