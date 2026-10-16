# SPDX-License-Identifier: Apache-2.0
"""The health of a project connector: one check, used wherever it must be known (S40-05).

A connector's `status` used to be computed by one route only, the *Test* button. Every save
reset it to `unknown`, so a freshly configured connector read "Unknown" until somebody pressed
a button. The check now runs when a connector is saved and when one of its secrets is set or
cleared, and still on *Test*. It is bounded in time, and a failure is recorded on the
connector, never raised: the configuration is kept and the card says why it fails.
"""

from __future__ import annotations

import asyncio

from choregos_core import utcnow
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Connector
from ..deps import ProjectContext
from ..schemas import ConnectorCheck, ConnectorTestResult
from ..secret_store import resolved_configuration


async def check_connector(session: AsyncSession, ctx: ProjectContext, row: Connector) -> ConnectorTestResult:
    """Build the connector's adapter, run its `test()`, store `status`, `last_check_at`, `last_error`."""
    from choregos_adapters import build, fakes_enabled
    from choregos_core.redaction import redact, redact_known

    timeout = get_settings().connector_test_timeout_s
    checks: list[ConnectorCheck] = []
    try:
        # Secrets are RESOLVED here, in this process: a reference it cannot read is a failed
        # test that names it, not an adapter that falls back to its default value.
        config = await resolved_configuration(
            session, ctx.project.org_id, row.kind, row.type, row.config, row.secret_refs, row.secret_ref
        )
        adapter = build(row.kind, row.type, config)
        checks.append(ConnectorCheck(name="construction", ok=True, detail=type(adapter).__name__))
        tester = getattr(adapter, "test", None)
        if tester is not None:
            result = await asyncio.wait_for(tester(), timeout=timeout)
            checks.append(
                ConnectorCheck(
                    name="connection", ok=bool(result.get("ok", True)), detail=redact_known(str(result))
                )
            )
        else:
            checks.append(
                ConnectorCheck(
                    name="connection",
                    ok=True,
                    detail="fakes active" if fakes_enabled() else "no specific test for this type",
                )
            )
    except TimeoutError:
        checks.append(
            ConnectorCheck(name="connection", ok=False, detail=f"the test did not answer in {timeout:g} s")
        )
    except Exception as exc:  # a connector error is never a 500
        checks.append(ConnectorCheck(name="connection", ok=False, detail=redact(str(exc))[:500]))

    ok = all(check.ok for check in checks)
    row.status = "ok" if ok else "error"
    row.last_check_at = utcnow()
    row.last_error = None if ok else redact("; ".join(c.detail or "" for c in checks if not c.ok))[:1000]
    return ConnectorTestResult(ok=ok, checks=checks)
