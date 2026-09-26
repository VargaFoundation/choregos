"""Templates de stack : catalogue, détail, publication (admin)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, status
from sqlalchemy import select

from ..audit import record
from ..db.models import Template
from ..deps import Db, Me
from ..errors import forbidden, not_found, unprocessable
from ..schemas import TemplateDetail, TemplateSummary, TemplateUpsert

router = APIRouter(tags=["templates"])


def _repertoire_des_templates() -> Path:
    """Le même dossier que l'orchestrateur, lu par le même réglage.

    Il y avait DEUX calculs pour un seul dossier : ici un `parents[5]` qui suppose la
    disposition du dépôt source — dans l'image c'est `/app`, et `templates/` n'y est pas —
    et là-bas `CHOREGOS_TEMPLATES_DIR`, ajouté précisément parce que le premier ne marchait
    pas. Deux lecteurs qui divergent, c'est une liste de templates qui dépend du processus
    qui la demande.
    """
    configure = os.environ.get("CHOREGOS_TEMPLATES_DIR", "").strip()
    return Path(configure) if configure else Path(__file__).resolve().parents[5] / "templates"


def _from_disk() -> list[TemplateDetail]:
    """Les templates livrés dans le dépôt sont visibles même sans base peuplée."""
    out: list[TemplateDetail] = []
    racine = _repertoire_des_templates()
    if not racine.is_dir():
        return out
    for manifest_path in sorted(racine.glob("*/manifest.yaml")):
        try:
            manifest: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        metadata = manifest.get("metadata", {})
        out.append(
            TemplateDetail(
                name=metadata.get("name", manifest_path.parent.name),
                version=metadata.get("version", "0.0.0"),
                display=metadata.get("display", metadata.get("name", "")),
                description=metadata.get("description"),
                is_published=True,
                manifest=manifest,
            )
        )
    return out


@router.get("/templates", response_model=list[TemplateSummary], operation_id="listTemplates")
async def list_templates(session: Db) -> list[TemplateSummary]:
    rows = (await session.execute(select(Template))).scalars().all()
    from_db = [
        TemplateSummary(
            name=row.name,
            version=row.version,
            display=(row.manifest.get("metadata", {}) or {}).get("display", row.name),
            description=(row.manifest.get("metadata", {}) or {}).get("description"),
            repo_url=row.repo_url,
            is_published=row.is_published,
        )
        for row in rows
    ]
    known = {t.name for t in from_db}
    return from_db + [
        TemplateSummary(**t.model_dump(exclude={"manifest"})) for t in _from_disk() if t.name not in known
    ]


@router.get("/templates/{name}", response_model=TemplateDetail, operation_id="getTemplate")
async def get_template(name: str, session: Db) -> TemplateDetail:
    row = (
        await session.execute(
            select(Template).where(Template.name == name).order_by(Template.version.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return TemplateDetail(
            name=row.name,
            version=row.version,
            display=(row.manifest.get("metadata", {}) or {}).get("display", row.name),
            description=(row.manifest.get("metadata", {}) or {}).get("description"),
            repo_url=row.repo_url,
            is_published=row.is_published,
            manifest=row.manifest,
        )
    for template in _from_disk():
        if template.name == name:
            return template
    raise not_found("Template", name)


def _validate_manifest(manifest: dict[str, Any]) -> None:
    import choregos_contracts as contracts
    from jsonschema import Draft202012Validator

    errors = sorted(
        Draft202012Validator(contracts.load_schema("template.schema.json")).iter_errors(manifest),
        key=lambda e: list(e.path),
    )
    if errors:
        raise unprocessable(
            "manifeste de template invalide",
            [{"loc": [".".join(str(p) for p in e.path)], "msg": e.message} for e in errors],
        )


@router.post(
    "/templates",
    response_model=TemplateSummary,
    status_code=status.HTTP_201_CREATED,
    operation_id="createTemplate",
)
async def create_template(body: TemplateUpsert, session: Db, principal: Me) -> TemplateSummary:
    if not principal.is_platform_admin():
        raise forbidden("publier un template demande le rôle org_admin")
    _validate_manifest(body.manifest)
    metadata = body.manifest.get("metadata", {})
    row = Template(
        name=metadata["name"],
        version=metadata["version"],
        manifest=body.manifest,
        repo_url=body.repo_url,
        is_published=body.is_published,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        principal,
        "template.create",
        org_id=None,  # un template est de plateforme, pas d'une organisation
        target_type="template",
        target_id=row.id,
        name=row.name,
    )
    return TemplateSummary(
        name=row.name,
        version=row.version,
        display=metadata.get("display", row.name),
        description=metadata.get("description"),
        repo_url=row.repo_url,
        is_published=row.is_published,
    )


@router.put("/templates/{name}", response_model=TemplateSummary, operation_id="updateTemplate")
async def update_template(name: str, body: TemplateUpsert, session: Db, principal: Me) -> TemplateSummary:
    if not principal.is_platform_admin():
        raise forbidden("modifier un template demande le rôle org_admin")
    _validate_manifest(body.manifest)
    metadata = body.manifest.get("metadata", {})
    row = (
        await session.execute(
            select(Template).where(Template.name == name, Template.version == metadata["version"])
        )
    ).scalar_one_or_none()
    if row is None:
        return await create_template(body, session, principal)
    row.manifest = body.manifest
    row.repo_url = body.repo_url
    row.is_published = body.is_published
    await record(
        session,
        principal,
        "template.update",
        org_id=None,  # idem
        target_type="template",
        target_id=row.id,
        name=row.name,
    )
    return TemplateSummary(
        name=row.name,
        version=row.version,
        display=metadata.get("display", row.name),
        description=metadata.get("description"),
        repo_url=row.repo_url,
        is_published=row.is_published,
    )
