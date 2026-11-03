"""Project templates ship with the image, and are found at run time."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_the_image_ships_the_templates() -> None:
    """`provisioning.py` reads `templates/<name>/manifest.yaml`: without this line, no project with a
    template provisions from a pod, and nothing said so before the first attempt."""
    dockerfile = (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    assert "COPY templates templates" in dockerfile


def test_the_templates_directory_is_configurable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from choregos_orchestrator.activities.provisioning import templates_dir

    assert (templates_dir() / "github-tekton-argo-k8s" / "manifest.yaml").exists()
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", "/app/templates")
    assert templates_dir() == pathlib.Path("/app/templates")
