# SPDX-License-Identifier: Apache-2.0
"""Setup procedures, served with this deployment's URLs filled in (ADR 0067).

Any signed-in member reads them: a procedure is the same text a person finds in `docs/procedures/`,
with the webhook and OAuth callback URLs of THIS platform instead of placeholders.
"""

from __future__ import annotations

from typing import Annotated

from choregos_core import procedures as core
from fastapi import APIRouter, HTTPException, Query

from ..deps import Config, Me
from ..schemas import ProcedureDto, ProcedureSection, ProcedureSummary, VendorDocDto

router = APIRouter(tags=["session"])


def _summary(proc: core.Procedure) -> ProcedureSummary:
    front = proc.front
    return ProcedureSummary(
        id=front.id,
        title=front.title,
        version=front.version,
        audience=front.audience,
        applies_to=dict(front.applies_to),
        link=front.link,
    )


@router.get("/procedures", response_model=list[ProcedureSummary], operation_id="listProcedures")
async def list_procedures(
    settings: Config,
    principal: Me,
    target: Annotated[
        str | None,
        Query(
            alias="for",
            description="`connector:documents/sharepoint`, `source:web`, `agent:remote.a2a`, "
            "`door_client:cursor`: the one best procedure for it, or none. Without `for`, all of them.",
        ),
    ] = None,
    card_host: Annotated[str | None, Query(description="the host of an agent card: selects a preset")] = None,
    preset: Annotated[str | None, Query(description="a catalogue preset, e.g. `atlassian-rovo`")] = None,
) -> list[ProcedureSummary]:
    catalogue = core.load(settings.procedures_extra)
    if target is None:
        return [_summary(p) for _, p in sorted(catalogue.procedures.items())]
    best = catalogue.for_target(target, card_host=card_host, preset=preset)
    return [_summary(best)] if best else []


@router.get("/procedures/{procedure_id}", response_model=ProcedureDto, operation_id="getProcedure")
async def get_procedure(
    procedure_id: str,
    settings: Config,
    principal: Me,
    org: Annotated[
        str | None, Query(description="fills `{{org}}`; by default the caller's only organisation")
    ] = None,
) -> ProcedureDto:
    proc = core.load(settings.procedures_extra).get(procedure_id)
    if proc is None:
        raise HTTPException(status_code=404, detail=f"no procedure `{procedure_id}`")
    slug = org or (next(iter(principal.org_roles)) if len(principal.org_roles) == 1 else "")
    ctx = core.Context(api_url=settings.api_url, public_url=settings.public_url, org=slug)
    return ProcedureDto(
        **_summary(proc).model_dump(),
        reviewed=proc.front.reviewed,
        sections=[
            ProcedureSection(key=s.key, title=s.title, markdown=s.markdown) for s in core.render(proc, ctx)
        ],
        values=core.values_of(proc, ctx),
        egress=list(proc.front.egress),
        fields=dict(proc.front.fields),
        vendor_docs=[VendorDocDto(title=d.title, url=d.url) for d in proc.front.vendor_docs],
        origin=proc.origin,
    )
