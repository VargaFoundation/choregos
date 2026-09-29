# SPDX-License-Identifier: Apache-2.0
"""Le quota des namespaces d'un projet vient-il du greffon, selon l'organisation — et du défaut sinon ?

Le greffon est installé pour de vrai (`.dist-info`) ; c'est `charger_les_greffons()` qui le trouve,
et le quota est lu par `_write_manifests`, l'étape de provisioning qui écrit dans le dépôt GitOps.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from choregos_contracts import ProjectConfig
from choregos_core import load_preset

GREFFON = """
from choregos_core.quotas import Quota, declarer_un_quota

def quota(org, projet):
    return Quota(cpu="4", memoire="8Gi", pods=6) if org == "petit" else None

def brancher():
    declarer_un_quota(quota)
"""


@pytest.fixture
def greffon(tmp_path: pathlib.Path) -> Iterator[None]:
    from choregos_adapters import charger_les_greffons
    from choregos_core.quotas import reinitialiser

    racine = tmp_path / "site"
    racine.mkdir()
    (racine / "greffon_quota.py").write_text(GREFFON, encoding="utf-8")
    info = racine / "greffon_quota-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: greffon_quota\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\ngreffon_quota = greffon_quota:brancher\n", encoding="utf-8"
    )
    sys.path.insert(0, str(racine))
    try:
        assert "greffon_quota" in charger_les_greffons()
        yield
    finally:
        sys.path.remove(str(racine))
        sys.modules.pop("greffon_quota", None)
        reinitialiser()


async def _quotas_ecrits(org: str) -> list[dict[str, Any]]:
    """Joue l'étape de provisioning qui écrit les manifestes, et rend les `ResourceQuota` écrits."""
    from choregos_orchestrator.activities.provisioning import _write_manifests

    ecrits: dict[str, str] = {}

    async def write_files(chemin: str, fichiers: dict[str, str]) -> None:
        ecrits.update(fichiers)

    config = ProjectConfig.model_validate(
        {
            "slug": "billing",
            "org": org,
            "repo": {"url": "https://github.com/x/billing.git", "default_branch": "main"},
        }
    )
    bundle = SimpleNamespace(
        slug="billing", org_slug=org, config=config, policy=load_preset("team"),
        adapters=SimpleNamespace(cd=SimpleNamespace(write_files=write_files)),
    )  # fmt: skip
    await _write_manifests(bundle, {}, SimpleNamespace(egress_image=""))
    documents = [d for texte in ecrits.values() for d in yaml.safe_load_all(texte) if d]
    return [d for d in documents if d["kind"] == "ResourceQuota"]


async def test_le_greffon_fixe_le_quota_de_son_organisation(greffon: None) -> None:
    quotas = await _quotas_ecrits("petit")
    assert len(quotas) == 2, "un quota par namespace (runners, ci)"
    for quota in quotas:
        dur = quota["spec"]["hard"]
        assert (dur["requests.cpu"], dur["limits.memory"], dur["count/pods"]) == ("4", "8Gi", "6")


async def test_une_autre_organisation_garde_le_defaut(greffon: None) -> None:
    for quota in await _quotas_ecrits("grand"):
        dur = quota["spec"]["hard"]
        assert (dur["requests.cpu"], dur["limits.memory"], dur["count/pods"]) == ("32", "96Gi", "40")


async def test_sans_greffon_le_defaut_historique() -> None:
    for quota in await _quotas_ecrits("petit"):
        assert quota["spec"]["hard"]["requests.cpu"] == "32"


@pytest.mark.parametrize(
    ("cpu", "memoire", "pods"), [("beaucoup", "8Gi", 6), ("4", "8 gigas", 6), ("4", "8Gi", 0)]
)
def test_un_quota_mal_ecrit_est_refuse_avant_argo(cpu: str, memoire: str, pods: int) -> None:
    from choregos_core.quotas import Quota

    with pytest.raises(ValueError, match="quota"):
        Quota(cpu=cpu, memoire=memoire, pods=pods)
