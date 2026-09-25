"""Les templates de projet sont livrés avec l'image, et trouvés à l'exécution."""

from __future__ import annotations

import pathlib

RACINE = pathlib.Path(__file__).resolve().parents[3]


def test_l_image_embarque_les_templates() -> None:
    """`provisioning.py` lit `templates/<nom>/manifest.yaml` : sans cette ligne, aucun projet à
    template ne se provisionne depuis un pod — et rien ne le disait avant le premier essai."""
    dockerfile = (RACINE / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    assert "COPY templates templates" in dockerfile


def test_le_repertoire_des_templates_se_configure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from choregos_orchestrator.activities.provisioning import repertoire_templates

    assert (repertoire_templates() / "github-tekton-argo-k8s" / "manifest.yaml").exists()
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", "/app/templates")
    assert repertoire_templates() == pathlib.Path("/app/templates")
