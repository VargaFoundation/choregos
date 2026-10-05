"""Ce qu'un gabarit livre à un greffon (S20-09) : `defaults.extensions: {<installateur>: <dossier>}`.

À la naissance du projet, le cœur lit le dossier — sans en sortir, sans lien symbolique — et le
remet à l'installateur déclaré sous ce nom, dans la transaction qui crée le projet : un refus de
l'installateur fait échouer la naissance. Sans installateur, le projet naît sans, et l'audit le dit.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"


@pytest.fixture
def gabarits(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Un dossier de gabarits à part, et un gabarit `essai-ext` qui livre l'extension `essai`."""
    racine = tmp_path / "gabarits"
    dossier = racine / "essai-ext"
    (dossier / "essai" / "sous").mkdir(parents=True)
    (dossier / "manifest.yaml").write_text(
        "apiVersion: choregos/v1\nkind: Template\nmetadata: {name: essai-ext, version: 1.0.0}\n"
        "defaults:\n  workflows: ['template:default-simple@1']\n  extensions: {essai: ./essai}\n",
        encoding="utf-8",
    )
    (dossier / "essai" / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    (dossier / "essai" / "sous" / "b.yaml").write_text("b: 2\n", encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(racine))
    return dossier


@pytest.fixture
def installateurs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Le registre des installateurs, propre au test."""
    from choregos_api import greffons

    registre: dict[str, Any] = {}
    monkeypatch.setattr(greffons, "_INSTALLATEURS", registre)
    return registre


async def _naitre(client: AsyncClient, slug: str = "rh") -> Any:
    return await client.post(
        f"{ORG}/projects",
        json={
            "slug": slug,
            "name": slug,
            "template_ref": "essai-ext",
            "config": {"slug": slug, "org": "varga"},
        },
    )


async def _audit(client: AsyncClient, action: str) -> list[dict[str, Any]]:
    return [a for a in (await client.get("/api/v1/audit")).json()["items"] if a["action"] == action]


async def test_l_installateur_declare_recoit_le_dossier_et_l_audit_le_dit(
    client: AsyncClient, admin: str, gabarits: pathlib.Path, installateurs: dict[str, Any]
) -> None:
    from choregos_api.greffons import declarer_un_installateur_de_gabarit

    recus: list[tuple[str, dict[str, str], str]] = []

    async def installer(session: Any, projet: Any, fichiers: dict[str, str], auteur: str) -> dict[str, Any]:
        recus.append((projet.id, fichiers, auteur))
        return {"installe": sorted(fichiers)}

    declarer_un_installateur_de_gabarit("essai", installer)
    cree = await _naitre(client)
    assert cree.status_code == 201, cree.text
    assert recus == [(cree.json()["id"], {"a.yaml": "a: 1\n", "sous/b.yaml": "b: 2\n"}, "template:essai-ext")]
    (ligne,) = await _audit(client, "template.extension.install")
    assert ligne["target_id"] == cree.json()["id"]
    assert ligne["payload"]["extension"] == "essai"
    assert ligne["payload"]["result"] == {"installe": ["a.yaml", "sous/b.yaml"]}
    assert await _audit(client, "template.extension.skip") == []


async def test_sans_installateur_le_projet_nait_sans_et_l_audit_le_dit(
    client: AsyncClient, admin: str, gabarits: pathlib.Path, installateurs: dict[str, Any]
) -> None:
    cree = await _naitre(client)
    assert cree.status_code == 201, cree.text
    (ligne,) = await _audit(client, "template.extension.skip")
    assert ligne["target_id"] == cree.json()["id"]
    assert ligne["payload"]["extension"] == "essai"
    assert "aucun greffon" in ligne["payload"]["reason"]


async def test_un_refus_de_l_installateur_fait_echouer_la_naissance(
    client: AsyncClient, admin: str, gabarits: pathlib.Path, installateurs: dict[str, Any]
) -> None:
    from choregos_api.errors import unprocessable
    from choregos_api.greffons import declarer_un_installateur_de_gabarit

    async def installer(session: Any, projet: Any, fichiers: dict[str, str], auteur: str) -> dict[str, Any]:
        raise unprocessable("paquet faux")

    declarer_un_installateur_de_gabarit("essai", installer)
    refus = await _naitre(client)
    assert refus.status_code == 422, refus.text
    assert "paquet faux" in refus.text
    assert "rh" not in {p["slug"] for p in (await client.get(f"{ORG}/projects")).json()["items"]}


@pytest.mark.parametrize("cas", ["hors du gabarit", "lien symbolique"])
async def test_un_dossier_qui_sort_du_gabarit_est_refuse(
    client: AsyncClient, admin: str, gabarits: pathlib.Path, installateurs: dict[str, Any], cas: str
) -> None:
    from choregos_api.greffons import declarer_un_installateur_de_gabarit

    recus: list[str] = []

    async def installer(session: Any, projet: Any, fichiers: dict[str, str], auteur: str) -> dict[str, Any]:
        recus.append(projet.id)
        return {}

    declarer_un_installateur_de_gabarit("essai", installer)
    secret = gabarits.parent.parent / "secret.yaml"
    secret.write_text("cle: valeur\n", encoding="utf-8")
    manifeste = gabarits / "manifest.yaml"
    if cas == "hors du gabarit":
        manifeste.write_text(manifeste.read_text().replace("./essai", "../../"), encoding="utf-8")
    else:
        (gabarits / "essai" / "lien.yaml").symlink_to(secret)
    refus = await _naitre(client)
    assert refus.status_code == 422, refus.text
    assert recus == []


def test_deux_greffons_ne_declarent_pas_le_meme_installateur(installateurs: dict[str, Any]) -> None:
    from choregos_api.greffons import (
        declarer_un_installateur_de_gabarit,
        installateurs_de_gabarit,
        reinitialiser,
    )

    async def un(*_: Any) -> dict[str, Any]:
        return {}

    async def autre(*_: Any) -> dict[str, Any]:
        return {}

    declarer_un_installateur_de_gabarit("essai", un)
    declarer_un_installateur_de_gabarit("essai", un)
    with pytest.raises(ValueError, match="déjà déclaré"):
        declarer_un_installateur_de_gabarit("essai", autre)
    reinitialiser()
    assert installateurs_de_gabarit() == {}
