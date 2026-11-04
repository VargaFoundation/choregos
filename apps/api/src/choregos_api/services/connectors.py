# SPDX-License-Identifier: Apache-2.0
"""What holds for every connector, project or organisation (ADR 0034): its secrets are written
only as references, and an operation only tightens as it goes down."""

from __future__ import annotations

from typing import Any

from choregos_adapters import POLICIES, ConnectorTypeSpec, OperationSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ConnectorOperation, OrgConnector, Project, ProjectOperationPolicy
from ..errors import unprocessable
from .catalogue_rights import connector_enabled_for

#: The rank of each policy: a project can only go up (ADR 0034).
RANK = {policy: rank for rank, policy in enumerate(POLICIES)}


def stricter(a: str, b: str | None) -> str:
    return a if b is None or RANK[a] >= RANK[b] else b


def declared_operation(connector: OrgConnector, operation: str) -> OperationSpec | None:
    """What is declared of one operation of this connector: by its plugin's server (ADR 0096), else
    by its type (ADR 0052); `None` for a discovered MCP tool."""
    from choregos_adapters import operation_spec

    server = connector.plugin_server
    if server is not None:
        found = (server.get("operations") or {}).get(operation)
        if not isinstance(found, dict):
            return None
        return OperationSpec(
            operation,
            str(found.get("access", "write")),
            str(found.get("description") or ""),
            found.get("input_schema"),
            human_approval=bool(found.get("human_approval")),
            compensation=found.get("compensation"),
            platform_arguments=tuple(found.get("platform_arguments") or ()),
        )
    return operation_spec(connector.kind, connector.type, operation)


def undeclared(connector: OrgConnector, operation: str) -> bool:
    """A tool a plugin's server serves that its `operations` do not declare: refused, whatever a row
    of `connector_operations` says (ADR 0096). Never true for another connector."""
    server = connector.plugin_server
    return server is not None and operation not in (server.get("operations") or {})


def human_floor(connector: OrgConnector, operation: str, policy: str) -> str:
    """An operation its type (or its plugin's server) declares `human_approval` (ADR 0052, ADR 0096)
    is never below `approval`: the organisation cannot open it to the policy, and a row edited behind
    the API does not either. An operation a plugin's server does not declare is `forbidden`."""
    if undeclared(connector, operation):
        return "forbidden"
    declared = declared_operation(connector, operation)
    if declared is not None and declared.human_approval:
        return stricter(policy, "approval")
    return policy


def type_spec(kind: str, type_name: str) -> ConnectorTypeSpec:
    from choregos_adapters import available, spec_of

    spec = spec_of(kind, type_name)
    if spec is None:
        raise unprocessable(
            f"no type `{type_name}` for `{kind}` (known: {', '.join(available(kind)) or 'none'})"
        )
    return spec


def spec_to_declare(kind: str, type_name: str) -> ConnectorTypeSpec:
    """`type_spec` for a NEW declaration: a demonstration type is refused in staging and prod,
    where its factory would refuse to build it anyway — say so now, not at the first call."""
    from choregos_adapters.registry import ENVIRONMENTS_WITHOUT_DEMOS

    from ..config import get_settings

    spec = type_spec(kind, type_name)
    environment = get_settings().env
    if spec.dev_only and environment in ENVIRONMENTS_WITHOUT_DEMOS:
        raise unprocessable(
            f"`{kind}: {type_name}` is a demonstration held in memory: it is refused in {environment}"
        )
    return spec


def check_type_config(kind: str, type_name: str, config: dict[str, Any]) -> None:
    """The type's own configuration check (S40-01k: a Firecrawl `base_url` is required and never the
    cloud): 422 with the adapter's message, before anything is written."""
    from choregos_adapters import validate_config
    from choregos_adapters.errors import AdapterError

    try:
        validate_config(kind, type_name, config)
    except AdapterError as refused:
        raise unprocessable(str(refused)) from refused


def check_runtime_allowed(type_name: str) -> None:
    """Refuse a `runtime` connector the deployment does not run (ADR 0072).

    A project could pick `tekton` on a cluster without Tekton, and its first run died. The
    deployment declares what it runs (`CHOREGOS_EXECUTOR_KIND`, `CHOREGOS_EXECUTORS_ALLOWED`);
    when it declares nothing (a bare dev server) nothing is refused."""
    from choregos_adapters.registry import allowed_executor_kinds, executors_declared

    if not executors_declared():
        return
    allowed = allowed_executor_kinds()
    if type_name not in allowed:
        raise unprocessable(
            f"this deployment runs agents on `{allowed[0]}`; allowed: {', '.join(allowed)}. "
            f"Ask the platform operator to add `{type_name}` to `global.runner.allowedExecutors`."
        )


def a_field_of(spec: ConnectorTypeSpec, name: str) -> bool:
    """A secret field of the type, or a slot of one it pools (`api_key.2`, ADR 0089)."""
    from choregos_adapters.registry import slot_of

    field, n = slot_of(name)
    return field in spec.secret_fields and (n == 1 or field in spec.pooled_fields)


def verify_secrets(
    spec: ConnectorTypeSpec,
    type_name: str,
    config: dict[str, Any],
    secret_refs: dict[str, str],
    secret_ref: str | None = None,
    *,
    may_reference: bool = True,
    secret_values: dict[str, str] | None = None,
    store_prefix: str | None = None,
) -> None:
    """A secret written in clear, an unknown secret field, an unreadable reference: 422.

    The secret fields of a type (Jira's `api_token`, Argo CD's `token`...) used to travel in
    `config`, in clear in the database and in every API response. They are now written as
    references (`secret_refs`), resolved when the adapter is built, or as VALUES (`secret_values`),
    sealed in the database and replaced by a derived `store:` reference (ADR 0058).

    A reference to the environment names a variable of the deployment, which every organisation's
    pods share (S39-06b, ADR 0088): only a platform administrator writes one (`may_reference`), even
    one already on the connector — re-sent with a new host, it would carry the operator's key there.
    A `store:` reference is never
    user-written: it is accepted only when it is exactly the one this connector's field derives from
    `store_prefix` (the idempotent re-send of a DTO). Otherwise a project owner could point a
    connector at `store:connector/entra-acme/client_secret` and read the organisation's secret
    through a host they control."""
    from choregos_core.secrets import InvalidReference, check, is_store_reference

    in_clear = sorted(set(config) & set(spec.secret_fields))
    if in_clear:
        raise unprocessable(
            f"secret written in clear: {', '.join(in_clear)}. A secret is typed as a `secret_values` "
            f"entry that the platform seals, or, for a key the operator holds, referenced in "
            f"`secret_refs` (`{in_clear[0]}: env:VARIABLE_NAME`) — never inside `config`"
        )
    values = secret_values or {}
    unknown = sorted(f for f in set(secret_refs) | set(values) if not a_field_of(spec, f))
    if unknown:
        pooled = (
            f"; several keys: {', '.join(f'{p}.2' for p in spec.pooled_fields)} …"
            if spec.pooled_fields
            else ""
        )
        raise unprocessable(
            f"`{type_name}` has no secret field {', '.join(unknown)} "
            f"(its own: {', '.join(spec.secret_fields) or 'none'}{pooled})"
        )
    doubled = sorted(set(secret_refs) & set(values))
    if doubled:
        raise unprocessable(
            f"{', '.join(doubled)}: a secret field is given as a value or as a reference, not both"
        )
    empty = sorted(f for f, v in values.items() if not v)
    if empty:
        raise unprocessable(f"{', '.join(empty)}: an empty secret value (to remove a secret, clear it)")
    written = [(f, r) for f, r in [*secret_refs.items(), *([("", secret_ref)] if secret_ref else [])]]
    for field, reference in written:
        if is_store_reference(reference):
            if field and store_prefix is not None and reference == f"store:{store_prefix}/{field}":
                continue
            raise unprocessable(
                "a `store:` reference is derived by the platform, never written: type the value "
                "(`secret_values`) to store a secret"
            )
        if not may_reference:
            raise unprocessable(
                "only a platform administrator writes a secret reference (`env:NAME`): it reads the "
                "deployment's environment, which every organisation shares; type the value instead"
            )
        try:
            check(reference)
        except InvalidReference as refus:
            raise unprocessable(str(refus)) from refus


async def policy_for_project(session: AsyncSession, project: Project, connector: str, operation: str) -> str:
    """The policy of an operation for THIS project (ADR 0034): the organisation's, tightened by
    the project. An unknown operation, or one whose groups do not meet the project's, is
    forbidden: what the broker hides from a run, an action does not play either."""
    found_row = (
        await session.execute(
            select(ConnectorOperation, OrgConnector)
            .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
            .where(
                OrgConnector.org_id == project.org_id,
                OrgConnector.name == connector,
                ConnectorOperation.name == operation,
            )
        )
    ).one_or_none()
    if found_row is None:
        return "forbidden"
    found, instance = found_row
    if not await connector_enabled_for(session, instance, project):
        return "forbidden"
    groups = {str(g) for g in ((project.config or {}).get("groups") or [])}
    if found.groups and not groups & set(found.groups):
        return "forbidden"
    tightened = (
        await session.execute(
            select(ProjectOperationPolicy.policy).where(
                ProjectOperationPolicy.project_id == project.id,
                ProjectOperationPolicy.operation_id == found.id,
            )
        )
    ).scalar_one_or_none()
    return human_floor(instance, operation, stricter(found.policy, tightened))
