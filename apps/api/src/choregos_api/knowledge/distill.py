# SPDX-License-Identifier: Apache-2.0
"""Distillation: a knowledge base compiled into a principles skill (S33-05, ADR 0051).

The `knowledge-distiller` agent of the catalogue (or the organisation's own version of it, once
installed) reads the base's converted documents and answers principles, each citing the documents
and sections it comes from. The platform checks every citation against the corpus — a principle
citing nothing real is dropped and reported — renders `SKILL.md` and `references/sources.md` (each
document with its sha256), and publishes the skill version **`pending_review`** whatever its risk
tier: machine-written guidance reaches no agent before a person approved it (S32-06, ADR 0050).

One model call, no tools: the corpus goes in the prompt, so it is capped (`MAX_CORPUS_CHARS`); a
larger base needs a distillation over its index, not done here. Under `CHOREGOS_FAKES` the distiller
is deterministic: one principle per section, its first sentence, citing that section.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from choregos_core import utcnow
from sqlalchemy import func, select
from sqlalchemy.orm import undefer

from ..config import Settings, get_settings
from ..db.models import KnowledgeBase, KnowledgeDocument, Skill, SkillVersion
from ..db.session import session_scope
from ..logging import get_logger
from .chunking import chunk

logger = get_logger("choregos.knowledge.distill")

AGENT = "knowledge-distiller"
#: About 100k tokens: the corpus goes whole into one prompt.
MAX_CORPUS_CHARS = 400_000
MAX_PRINCIPLES = 60
_SENTENCE = re.compile(r"(.+?[.!?])(\s|$)", re.DOTALL)


class DistillationError(RuntimeError):
    """The base cannot be distilled, or the answer holds nothing usable; the message says why."""


@dataclass(frozen=True)
class Source:
    filename: str
    title: str
    digest: str
    markdown: str


class Distiller(Protocol):
    model: str

    async def principles(self, instructions: str, corpus: list[Source]) -> dict[str, Any]: ...


def corpus_text(corpus: list[Source]) -> str:
    return "\n\n".join(f"=== DOCUMENT: {s.filename} | {s.title} ===\n{s.markdown}" for s in corpus)


class FakeDistiller:
    """Deterministic: one principle per section with text, its first sentence, citing that section."""

    model = "fake/distiller"

    async def principles(self, instructions: str, corpus: list[Source]) -> dict[str, Any]:
        found: list[dict[str, Any]] = []
        for source in corpus:
            for part in chunk(source.markdown, source.title)[:5]:
                first = _SENTENCE.match(part.body.strip())
                statement = (first.group(1) if first else part.body.strip().splitlines()[0])[:300]
                found.append(
                    {
                        "title": (part.heading_path or [source.title])[-1],
                        "statement": statement,
                        "rationale": f"Stated in {source.title}.",
                        "sources": [{"document": source.filename, "section": " > ".join(part.heading_path)}],
                    }
                )
        return {
            "description": "Principles of the knowledge base; apply them and cite them.",
            "principles": found,
        }


class GatewayDistiller:
    """One chat completion on the LiteLLM gateway, answering JSON."""

    def __init__(self, base_url: str, key: str, model: str, *, timeout: float = 300.0) -> None:
        self.base_url, self.key, self.model, self.timeout = base_url.rstrip("/"), key, model, timeout

    async def principles(self, instructions: str, corpus: list[Source]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": corpus_text(corpus)},
                    ],
                    "response_format": {"type": "json_object"},
                    "metadata": {"choregos_agent": AGENT},
                },
            )
        if response.status_code >= 400:
            raise DistillationError(f"the gateway answered {response.status_code}: {response.text[:300]}")
        content = response.json()["choices"][0]["message"]["content"]
        try:
            return dict(json.loads(content))
        except (json.JSONDecodeError, TypeError) as exc:
            raise DistillationError("the distiller did not answer one JSON object") from exc


def get_distiller(settings: Settings | None = None) -> Distiller:
    settings = settings or get_settings()
    if settings.fakes:
        return FakeDistiller()
    if not settings.gateway_master_key:
        raise DistillationError("no gateway key: the distiller has no model to call")
    model = settings.default_model_profiles.get("standard") or "platform/standard"
    return GatewayDistiller(settings.gateway_url, settings.gateway_master_key, model)


async def instructions_for(session: Any, org_id: str, base: KnowledgeBase) -> str:
    """The organisation's `knowledge-distiller` (latest version) if installed, else the catalogue's;
    rendered with the base as its work item."""
    from choregos_core.catalogue_d_agents import entree
    from choregos_core.instructions import rendre_les_instructions

    from ..services.agent_releases import production_spec_of

    source = await production_spec_of(session, org_id, AGENT, active_only=True)
    text = str((source or {}).get("instructions") or entree(AGENT).document["spec"]["instructions"])  # type: ignore[union-attr]
    return rendre_les_instructions(
        text,
        ticket={
            "key": f"KB-{base.slug}",
            "title": f"Distil the knowledge base “{base.name}”",
            "body": base.description or "",
        },
        spec="",
        plan_markdown="",
        allowed_paths=[],
        inputs={},
    )


def checked(answer: dict[str, Any], corpus: list[Source]) -> tuple[list[dict[str, Any]], list[str]]:
    """The principles whose citations point into the corpus, and what was dropped and why."""
    known = {s.filename.lower(): s for s in corpus} | {s.title.lower(): s for s in corpus}
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for raw in list(answer.get("principles") or [])[: MAX_PRINCIPLES * 2]:
        if not isinstance(raw, dict) or not str(raw.get("statement") or "").strip():
            dropped.append("a principle without a statement")
            continue
        sources = []
        for cited in raw.get("sources") or []:
            document = known.get(str((cited or {}).get("document", "")).strip().lower())
            if document is not None:
                sources.append(
                    {"document": document.filename, "section": str(cited.get("section") or "").strip()}
                )
        title = str(raw.get("title") or raw["statement"])[:120].strip()
        if not sources:
            dropped.append(f"“{title}”: cites no document of the base")
            continue
        kept.append(
            {
                "title": title,
                "statement": str(raw["statement"]).strip(),
                "rationale": str(raw.get("rationale") or "").strip(),
                "sources": sources,
            }
        )
        if len(kept) >= MAX_PRINCIPLES:
            break
    return kept, dropped


def render(slug: str, base: KnowledgeBase, description: str, principles: list[dict[str, Any]],
           corpus: list[Source]) -> dict[str, str]:  # fmt: skip
    """`SKILL.md` and `references/sources.md`: each principle with its citations, each document with
    its digest — the skill says where every line of it came from."""
    summary = description or f"Principles of the knowledge base {base.name}"
    lines = [
        "---",
        f"name: {slug}",
        f"description: {json.dumps(summary, ensure_ascii=False)}",
        "---",
        "",
        f"# Principles — {base.name}",
        "",
        f"Distilled from the knowledge base `{base.slug}` ({len(corpus)} documents, listed with their "
        "digest in `references/sources.md`). Apply each principle that concerns your work, and cite it "
        "with its source.",
        "",
    ]
    for number, principle in enumerate(principles, start=1):
        cited = "; ".join(
            f"`{s['document']}` § {s['section']}" if s["section"] else f"`{s['document']}`"
            for s in principle["sources"]
        )
        lines += [f"{number}. **{principle['title']}** — {principle['statement']}"]
        if principle["rationale"]:
            lines.append(f"   - Why: {principle['rationale']}")
        lines += [f"   - Sources: {cited}", ""]
    references = ["# Sources", "", "| document | title | sha256 |", "| --- | --- | --- |"]
    references += [f"| `{s.filename}` | {s.title} | `{s.digest}` |" for s in corpus]
    return {
        "SKILL.md": "\n".join(lines).rstrip() + "\n",
        "references/sources.md": "\n".join(references) + "\n",
    }


async def load_corpus(session: Any, base: KnowledgeBase) -> list[Source]:
    documents = (
        await session.execute(
            select(KnowledgeDocument)
            .options(undefer(KnowledgeDocument.markdown))
            .where(KnowledgeDocument.knowledge_base_id == base.id, KnowledgeDocument.status == "ready")
            .order_by(KnowledgeDocument.filename, KnowledgeDocument.id)
        )
    ).scalars()
    corpus = [Source(d.filename, d.title, d.digest, d.markdown or "") for d in documents]
    if not corpus:
        raise DistillationError("the knowledge base has no converted document")
    size = sum(len(s.markdown) for s in corpus)
    if size > MAX_CORPUS_CHARS:
        raise DistillationError(
            f"the corpus holds {size} characters, more than {MAX_CORPUS_CHARS}: "
            "too large to distil in one pass"
        )
    return corpus


async def distill(
    org: str, base_id: str, requested_by: str, *, distiller: Distiller | None = None
) -> dict[str, Any]:
    """Distils one base and publishes the skill version `pending_review`; records the outcome on the base."""
    from ..services.skill_gate import assess
    from ..services.skill_gate import columns as gate_columns
    from ..services.skills import empreinte, valider

    try:
        async with session_scope(orgs=[org]) as session:
            base = await session.get(KnowledgeBase, base_id)
            if base is None:
                return {"status": "gone"}
            corpus = await load_corpus(session, base)
            instructions = await instructions_for(session, base.org_id, base)
            distiller = distiller or get_distiller()
            # The organisation's catalogue decides whether this model may distil (ADR 0060).
            from .embeddings import ModelNotAllowedError, check_model_usage

            try:
                await check_model_usage(session, base.org_id, distiller.model, "distill")
            except ModelNotAllowedError as refusal:
                raise DistillationError(str(refusal)) from refusal
        answer = await distiller.principles(instructions, corpus)
        principles, dropped = checked(answer, corpus)
        if not principles:
            raise DistillationError("no principle cites a document of the base: nothing to publish")
        slug = f"{base.slug}-principles"[:63]
        files = render(slug, base, str(answer.get("description") or ""), principles, corpus)
        valider(files, slug)
        async with session_scope(orgs=[org]) as session:
            base = await session.get(KnowledgeBase, base_id)
            assert base is not None
            skill = (
                await session.execute(select(Skill).where(Skill.org_id == base.org_id, Skill.slug == slug))
            ).scalar_one_or_none()
            if skill is None:
                skill = Skill(org_id=base.org_id, slug=slug, status="active")
                session.add(skill)
                await session.flush()
            skill.description = f"Principles distilled from the knowledge base {base.name}"
            latest = (
                await session.execute(
                    select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill.id)
                )
            ).scalar() or 0
            gate = gate_columns(assess(files))
            # Machine-written guidance: a person reviews it, whatever its risk tier.
            gate["review_status"] = "pending_review"
            version = SkillVersion(
                skill_id=skill.id,
                org_id=base.org_id,
                version=latest + 1,
                files=files,
                digest=empreinte(files),
                created_by=f"agent:{AGENT}",
                **gate,
            )
            session.add(version)
            await session.flush()
            outcome = {
                "status": "pending_review",
                "skill": slug,
                "version": version.version,
                "principles": len(principles),
                "dropped": dropped[:20],
                "at": utcnow().isoformat(),
                "requested_by": requested_by,
                # `SkillVersion.source` describes a git import (S32-05): a distillation's provenance is
                # kept here, and in the skill's own `references/sources.md`.
                "model": distiller.model,
                "documents": [{"filename": s.filename, "digest": s.digest} for s in corpus],
            }
            base.distillation = outcome
    except DistillationError as exc:
        outcome = {
            "status": "failed",
            "error": str(exc),
            "at": utcnow().isoformat(),
            "requested_by": requested_by,
        }
        async with session_scope(orgs=[org]) as session:
            failed = await session.get(KnowledgeBase, base_id)
            if failed is not None:
                failed.distillation = outcome
    logger.info("knowledge.distilled", org=org, base=base_id, status=outcome["status"])
    return outcome
