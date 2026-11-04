# SPDX-License-Identifier: Apache-2.0
"""The hand-off receipts between stages (S25-04).

A stage produces outputs (a spec, a plan, the shortlisted profiles) that the next stage reads: in
its prompt, which the orchestrator renders when the run is created, then through `get_ticket`
during the run. Nothing said WHICH revision the next stage had read: an output rewritten in the
meantime, by a resume or by hand, went through without a trace. So every output stored leaves a
"produced" receipt, under its digest, and every read a "read" receipt, under the digest of what
was read. A receipt never changes: a new revision makes a new receipt.

Writing a receipt is idempotent (ADR 0008): the constraint (work item, output, digest, run, kind)
decides, and a replayed activity or two simultaneous reads make one receipt only.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from choregos_core.compat import alias_names
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ArtifactRecord, HandOffReceipt, Run, WorkItem
from ..schemas import HandOff, HandOffEvent

PRODUCED = "produced"
READ = "read"

#: The software documents a stage reads in its prompt when its template cites them.
PROMPT_DOCUMENTS = ("spec_markdown", "plan_markdown")


def digest(value: Any) -> str:
    """The digest of an output: the SHA-256 of its canonical JSON (sorted keys, no spaces)."""
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


async def record(
    session: AsyncSession,
    *,
    org_id: str,
    work_item_id: str,
    run_id: str,
    stage: str | None,
    genre: str,
    outputs: dict[str, Any] | None = None,
    sorties: dict[str, Any] | None = None,  # lang: fr-ok alias:internal
) -> int:
    """One receipt per present output; returns the number of receipts written (zero when all existed).

    `outputs` was `sorties`: the former keyword still works (ADR 0098 §3 (e))."""
    given = outputs if outputs is not None else sorties
    if given is None:
        raise TypeError("record() needs `outputs`")
    return await record_digests(
        session,
        org_id=org_id,
        work_item_id=work_item_id,
        run_id=run_id,
        stage=stage,
        digests={name: digest(value) for name, value in given.items() if _present(value)},
        genre=genre,
    )


async def record_digests(
    session: AsyncSession,
    *,
    org_id: str,
    work_item_id: str,
    run_id: str,
    stage: str | None,
    digests: dict[str, str],
    genre: str,
) -> int:
    """One receipt per output under a digest already computed: a file artifact's sha256 (ADR 0049)."""
    written = 0
    for name, value_digest in digests.items():
        existing = (
            await session.execute(
                select(HandOffReceipt.id).where(
                    HandOffReceipt.work_item_id == work_item_id,
                    HandOffReceipt.output == name,
                    HandOffReceipt.digest == value_digest,
                    HandOffReceipt.run_id == run_id,
                    HandOffReceipt.kind == genre,
                )
            )
        ).first()
        if existing is not None:
            continue
        try:
            # Two simultaneous reads of the same run both pass the check above: the constraint
            # decides, and the second does nothing.
            async with session.begin_nested():
                session.add(
                    HandOffReceipt(
                        org_id=org_id,
                        work_item_id=work_item_id,
                        output=name,
                        digest=value_digest,
                        kind=genre,
                        run_id=run_id,
                        stage=stage,
                    )
                )
                await session.flush()
        except IntegrityError:
            continue
        written += 1
    return written


def read_by_the_prompt(documents: dict[str, Any], inputs: list[str], prompt: str) -> dict[str, Any]:
    """What a run's prompt carries from the earlier stages: the inputs its transition declares
    (`inputs: [profiles]`), and the spec or the plan only when their text is IN the rendered prompt:
    a template that does not cite them does not make them read."""
    read = {name: documents.get(name) for name in inputs if _present(documents.get(name))}
    for name in PROMPT_DOCUMENTS:
        value = documents.get(name)
        if isinstance(value, str) and value.strip() and value in prompt:
            read[name] = value
    return read


async def work_item_handoffs(session: AsyncSession, item: WorkItem) -> list[HandOff]:
    """A work item's receipts, output by output, with the digest of what it is worth TODAY: an
    output modified afterwards no longer has the one of its last "produced" receipt."""
    receipts = (
        (
            await session.execute(
                select(HandOffReceipt)
                .where(HandOffReceipt.work_item_id == item.id)
                .order_by(HandOffReceipt.created_at, HandOffReceipt.kind.desc())
            )
        )
        .scalars()
        .all()
    )
    attempts = {
        run.id: run.attempt
        for run in (
            await session.execute(select(Run).where(Run.id.in_({r.run_id for r in receipts})))
        ).scalars()
    }
    documents = item.documents or {}
    # A file output (ADR 0049) is not in `documents`: what it is worth today is its latest revision.
    files = {
        row.name: row.digest
        for row in (
            await session.execute(
                select(ArtifactRecord)
                .where(ArtifactRecord.work_item_id == item.id, ArtifactRecord.storage_key.is_not(None))
                .order_by(ArtifactRecord.version)
            )
        ).scalars()
    }
    passages: dict[str, HandOff] = {}
    for receipt in receipts:
        value = documents.get(receipt.output)
        passage = passages.setdefault(
            receipt.output,
            HandOff(
                output=receipt.output,
                current_digest=digest(value) if _present(value) else files.get(receipt.output),
                events=[],
            ),
        )
        passage.events.append(
            HandOffEvent(
                kind=receipt.kind,
                digest=receipt.digest,
                run_id=receipt.run_id,
                stage=receipt.stage,
                attempt=attempts.get(receipt.run_id),
                at=receipt.created_at,
            )
        )
    return list(passages.values())


PRODUITE = PRODUCED  # lang: fr-ok alias:internal
LUE = READ  # lang: fr-ok alias:internal
DOCUMENTS_DU_PROMPT = PROMPT_DOCUMENTS  # lang: fr-ok alias:internal
empreinte = digest  # lang: fr-ok alias:internal
consigner = record  # lang: fr-ok alias:internal
lu_par_le_prompt = read_by_the_prompt  # lang: fr-ok alias:internal
passages_du_ticket = work_item_handoffs  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "PRODUITE": "PRODUCED",  # lang: fr-ok alias:internal
        "LUE": "READ",  # lang: fr-ok alias:internal
        "DOCUMENTS_DU_PROMPT": "PROMPT_DOCUMENTS",  # lang: fr-ok alias:internal
        "empreinte": "digest",  # lang: fr-ok alias:internal
        "consigner": "record",  # lang: fr-ok alias:internal
        "lu_par_le_prompt": "read_by_the_prompt",  # lang: fr-ok alias:internal
        "passages_du_ticket": "work_item_handoffs",  # lang: fr-ok alias:internal
    },
)
