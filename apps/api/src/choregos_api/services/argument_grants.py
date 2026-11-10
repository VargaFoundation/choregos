# SPDX-License-Identifier: Apache-2.0
"""Where an argument constraint is written (S60-06, ADR 0128): refused when it is written, not
discovered at the first call (ADR 0101). The pure rules are `choregos_core.argument_grants`."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from typing import Any

from choregos_core.argument_grants import constraint_error, properties_named
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ConnectorOperation, OrgConnector
from ..errors import unprocessable
from ..schemas import AgentSpec
from ..schemas.agents import AgentToolGrant


def refuse_constraint(
    operation_name: str, input_schema: dict[str, Any] | None, schema: Any, where: str
) -> None:
    """422 unless `schema` is a constraint the platform reads and names only arguments the operation has."""
    problem = constraint_error(schema)
    if problem is None:
        declared = set((input_schema or {}).get("properties") or {})
        unknown = sorted(properties_named(schema) - declared)
        if unknown:
            problem = (
                f"{operation_name} has no argument {', '.join(f'`{name}`' for name in unknown)} "
                f"(its input schema declares: {', '.join(sorted(declared)) or 'none'})"
            )
    if problem is not None:
        raise unprocessable(
            f"{where}: {problem}", [{"loc": [*where.split("."), "arguments"], "msg": problem}]
        )


async def check_agent_grants(session: AsyncSession, org_id: str, spec: AgentSpec) -> None:
    """A version's constrained grants (`{name, arguments}`) each match an operation of an
    organisation connector, name only arguments those operations have, and do not overlap: two
    constrained grants on one operation would both bind (their intersection), which is rarely what
    was written — one grant with `anyOf` says it."""
    for index, server in enumerate(spec.mcp_servers):
        grants = [tool for tool in server.tools if isinstance(tool, AgentToolGrant)]
        if not grants:
            continue
        operations = await _operations(session, org_id, server.connector)
        taken: dict[str, str] = {}
        for grant in grants:
            where = f"mcp_servers.{index}.tools.{grant.name}"
            matched = [op for op in operations if fnmatchcase(op.name, grant.name)]
            if not matched:
                raise unprocessable(
                    f"{where}: `{grant.name}` matches no operation of the organisation's connector "
                    f"`{server.connector}`: an argument constraint binds nothing there",
                    [{"loc": ["mcp_servers", index, "tools"], "msg": "no operation to bind"}],
                )
            for operation in matched:
                refuse_constraint(operation.name, operation.input_schema, grant.arguments, where)
                if operation.name in taken:
                    raise unprocessable(
                        f"{where}: `{operation.name}` is already bound by `{taken[operation.name]}`: "
                        "write one grant, with `anyOf` for the alternatives",
                        [{"loc": ["mcp_servers", index, "tools"], "msg": "two constraints on one operation"}],
                    )
                taken[operation.name] = grant.name


async def _operations(session: AsyncSession, org_id: str, connector: str) -> Iterable[ConnectorOperation]:
    return (
        (
            await session.execute(
                select(ConnectorOperation)
                .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
                .where(OrgConnector.org_id == org_id, OrgConnector.name == connector)
            )
        )
        .scalars()
        .all()
    )


__all__ = ["check_agent_grants", "refuse_constraint"]
