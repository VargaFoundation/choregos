"""Do the deployment and the image speak of the same edition?

Choregos exists in two editions ([ADR 0024](../../docs/adr/0024-two-editions.md)) and the edition
that runs is **not** a chart value: it is the enterprise extension that declares it when
loading. Nothing linked the two. An operator who writes `global.edition: enterprise` on
community images got a platform that starts, looks healthy, and refuses the second
organization -- the symptom arriving weeks after the cause, on an apparently unrelated action.

Two guards, at the two places where the gap can arise: the chart render refuses an enterprise
edition without a pull secret (the registry is private, that is all of phase 1's access
control), and the API refuses to start if what is announced is not what is loaded.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*surcharges: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        command += ["--set", surcharge]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_an_enterprise_edition_without_a_draw_secret_is_refused() -> None:
    """Without the secret, the pods would stay in `ImagePullBackOff` without ever naming the cause."""
    rendu = _render("global.edition=enterprise")
    assert rendu.returncode != 0, "the chart rendered an enterprise edition without a pull secret"
    assert "global.imagePullSecrets" in rendu.stderr, (
        f"the refusal does not name the missing value: {rendu.stderr[-400:]}"
    )


def test_an_unknown_edition_is_refused() -> None:
    """`pro`, `pricey`, `Enterprise`: a typo must not yield a silent deployment."""
    rendu = _render("global.edition=pro")
    assert rendu.returncode != 0
    assert "community" in rendu.stderr and "enterprise" in rendu.stderr


def test_the_enterprise_edition_with_its_secret_renders() -> None:
    """The guard must not forbid the enterprise edition -- only announcing it halfway."""
    rendu = _render("global.edition=enterprise", "global.imagePullSecrets[0].name=harbor-prive")
    assert rendu.returncode == 0, rendu.stderr[-400:]
    assert 'value: "enterprise"' in rendu.stdout


def test_every_process_receives_the_announced_edition() -> None:
    """The API and the workers must receive it: that is where the startup refusal happens.

    A Deployment added tomorrow without the `commonEnv` helper would not know which edition is announced,
    and would start as community without saying so.
    """
    rendu = _render()
    assert rendu.returncode == 0, rendu.stderr[-400:]
    sans: list[str] = []
    for doc in yaml.safe_load_all(rendu.stdout):
        if not doc or doc.get("kind") not in {"Deployment", "StatefulSet", "DaemonSet"}:
            continue
        name = doc["metadata"]["name"]
        if "choregos-web" in name:  # the front end reads no platform setting
            continue
        conteneurs = doc["spec"]["template"]["spec"].get("containers", [])
        names = {v["name"] for c in conteneurs for v in (c.get("env") or [])}
        if "CHOREGOS_EDITION" not in names:
            sans.append(name)
    assert not sans, f"these processes do not know which edition is announced: {sans}"
