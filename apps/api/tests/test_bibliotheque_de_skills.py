"""La bibliothèque de skills (ADR 0033, S18-04).

Une skill est un dossier qu'un agent porte. Elle ne déclare aucune permission, ne sort pas de son
dossier, et ses versions ne se réécrivent pas.
"""

from __future__ import annotations

import io
import stat
import zipfile
from typing import Any

import pytest
from httpx import AsyncClient

SKILL_MD = """---
name: procedure-onboarding
description: La procédure d'arrivée d'un collaborateur, étape par étape.
---

# Procédure d'arrivée

1. Créer le compte dans Entra.
"""
FICHIERS = {"SKILL.md": SKILL_MD, "references/groupes.md": "- tous-salaries\n- vpn\n"}


def _zip(fichiers: dict[str, str], *, lien: str | None = None) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        for chemin, texte in fichiers.items():
            archive.writestr(chemin, texte)
        if lien:
            info = zipfile.ZipInfo(lien)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    return tampon.getvalue()


async def _importer(client: AsyncClient, contenu: bytes) -> Any:
    return await client.post(
        "/api/v1/orgs/varga/skills/import", content=contenu, headers={"Content-Type": "application/zip"}
    )


async def test_une_skill_nait_et_ses_versions_ne_se_reecrivent_pas(client: AsyncClient, admin: str) -> None:
    cree = await client.post("/api/v1/orgs/varga/skills", json={"files": FICHIERS})
    assert cree.status_code == 201, cree.text
    assert (cree.json()["slug"], cree.json()["latest_version"]) == ("procedure-onboarding", 1)
    v1 = (await client.get("/api/v1/orgs/varga/skills/procedure-onboarding/versions/1")).json()
    assert v1["files"] == FICHIERS and v1["digest"].startswith("sha256:")
    v2 = await client.post(
        "/api/v1/orgs/varga/skills/procedure-onboarding/versions",
        json={"files": {**FICHIERS, "references/groupes.md": "- tous-salaries\n"}},
    )
    assert v2.status_code == 201 and v2.json()["version"] == 2
    assert (await client.get("/api/v1/orgs/varga/skills/procedure-onboarding/versions/1")).json() == v1
    assert (await client.post("/api/v1/orgs/varga/skills", json={"files": FICHIERS})).status_code == 409


async def test_une_archive_avec_son_dossier_racine_s_importe(client: AsyncClient, admin: str) -> None:
    importee = await _importer(client, _zip({f"procedure-onboarding/{c}": t for c, t in FICHIERS.items()}))
    assert importee.status_code == 201, importee.text
    fichiers = (await client.get("/api/v1/orgs/varga/skills/procedure-onboarding/versions/1")).json()["files"]
    assert set(fichiers) == {"SKILL.md", "references/groupes.md"}
    # La même archive, réimportée : la version suivante, pas un doublon.
    assert (await _importer(client, _zip(FICHIERS))).json()["latest_version"] == 2


@pytest.mark.parametrize(
    ("contenu", "motif"),
    [
        pytest.param(_zip({**FICHIERS, "../../evasion.sh": "rm -rf /"}), "goes outside", id="zip-slip"),
        pytest.param(_zip(FICHIERS, lien="references/lien"), "symbolic link", id="lien-symbolique"),
        pytest.param(
            _zip({"SKILL.md": SKILL_MD.replace("description:", "allowed-tools: [Bash]\ndescription:")}),
            "declares no permission",
            id="allowed-tools",
        ),
        pytest.param(
            _zip({"SKILL.md": SKILL_MD.replace("name: procedure-onboarding\n", "")}), "name", id="sans-nom"
        ),
        pytest.param(_zip({"lisez-moi.md": "x"}), "SKILL.md is missing", id="sans-skill-md"),
        pytest.param(_zip({**FICHIERS, **{f"f{i}.md": "x" for i in range(64)}}), "64", id="trop-de-fichiers"),
        pytest.param(_zip({**FICHIERS, "gros.md": "x" * (600 * 1024)}), "KiB", id="trop-gros"),
        pytest.param(b"pas une archive", "zip", id="pas-un-zip"),
    ],
)
async def test_ce_qu_une_archive_ne_fait_pas_passer(
    client: AsyncClient, admin: str, contenu: bytes, motif: str
) -> None:
    refuse = await _importer(client, contenu)
    assert refuse.status_code == 422, refuse.text
    assert motif in refuse.text
    assert (await client.get("/api/v1/orgs/varga/skills")).json() == [], "rien n'est créé"


async def test_le_nom_de_la_version_suivante_doit_etre_celui_de_la_skill(
    client: AsyncClient, admin: str
) -> None:
    await client.post("/api/v1/orgs/varga/skills", json={"files": FICHIERS})
    autre = {"SKILL.md": SKILL_MD.replace("name: procedure-onboarding", "name: autre-chose")}
    refuse = await client.post(
        "/api/v1/orgs/varga/skills/procedure-onboarding/versions", json={"files": autre}
    )
    assert refuse.status_code == 422 and "must match" in refuse.text


async def test_une_skill_dit_quels_agents_la_portent(client: AsyncClient, admin: str) -> None:
    await client.post("/api/v1/orgs/varga/skills", json={"files": FICHIERS})
    agent = await client.post(
        "/api/v1/orgs/varga/agents",
        json={
            "slug": "coordinateur-onboarding",
            "display_name": "Coordinateur",
            "spec": {"skills": [{"slug": "procedure-onboarding"}]},
        },
    )
    assert agent.status_code == 201, agent.text
    skill = (await client.get("/api/v1/orgs/varga/skills/procedure-onboarding")).json()
    assert skill["used_by"] == ["coordinateur-onboarding@1"]


async def test_le_runner_lit_les_skills_de_son_run_et_d_aucun_autre(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """`GET /internal/runs/{id}/skills` : les fichiers que le `StageInput` nomme, au jeton du run."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    cree = (await client.post("/api/v1/orgs/varga/skills", json={"files": FICHIERS})).json()
    digest = cree["versions"][0]["digest"]
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-9", title="T", state="ready")
        session.add(item)
        await session.flush()
        for run_id, skills in (
            ("run-avec", [{"slug": "procedure-onboarding", "version": 1, "digest": digest}]),
            ("run-sans", []),
        ):
            session.add(
                Run(
                    id=run_id,
                    work_item_id=item.id,
                    project_id=project["id"],
                    stage_role="implement",
                    status="running",
                    stage_input={"skills": skills},
                )
            )
    client.cookies.clear()
    jeton = mint_run_token("run-avec", project_slug="billing-api", work_item_key="BILLING-9", ttl_minutes=30)
    lues = await client.get(
        "/api/v1/internal/runs/run-avec/skills", headers={"Authorization": f"Bearer {jeton}"}
    )
    assert lues.status_code == 200, lues.text
    (skill,) = lues.json()
    assert (skill["slug"], skill["version"], skill["digest"], skill["files"]) == (
        "procedure-onboarding",
        1,
        digest,
        FICHIERS,
    )
    autre = await client.get(
        "/api/v1/internal/runs/run-sans/skills", headers={"Authorization": f"Bearer {jeton}"}
    )
    assert autre.status_code in {401, 403}, "le jeton d'un run ne lit pas un autre run"
