"""Our pods satisfy the strict Kyverno rule -- checked on the rendered chart.

`infra/policies/pod-security.yaml` requires, for every pod in a `choregos-*` or
`proj-*` namespace: `runAsNonRoot`, a seccomp profile at pod level, and for each container
`allowPrivilegeEscalation: false` and `capabilities.drop: [ALL]`. A strict rule that
would refuse our own pods would be a chart that does not install: this test renders the chart
for each environment and applies the rule to each pod template. Without `helm`, it
skips and says so.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHART = RACINE / "charts" / "choregos"
ENVIRONNEMENTS = ("local", "dev", "staging", "prod")

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendu(env: str) -> list[dict[str, Any]]:
    resultat = subprocess.run(
        ["helm", "template", "choregos", str(CHART), "-f", str(CHART / "values" / f"{env}.yaml")],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return [d for d in yaml.safe_load_all(resultat.stdout) if d]


def _modeles_de_pod(documents: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    sortie: list[tuple[str, dict[str, Any]]] = []
    for doc in documents:
        kind, nom = doc.get("kind"), doc.get("metadata", {}).get("name", "?")
        if kind in {"Deployment", "StatefulSet", "Job", "Rollout"}:
            sortie.append((f"{kind}/{nom}", doc["spec"]["template"]["spec"]))
        elif kind == "CronJob":
            sortie.append((f"{kind}/{nom}", doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]))
        elif kind == "Pod":
            sortie.append((f"{kind}/{nom}", doc["spec"]))
    return sortie


def manquements(spec: dict[str, Any]) -> list[str]:
    """What the `non-root` rule would fault this pod for; empty if it passes."""
    fautes: list[str] = []
    pod = spec.get("securityContext") or {}
    if pod.get("runAsNonRoot") is not True:
        fautes.append("pod.runAsNonRoot")
    if (pod.get("seccompProfile") or {}).get("type") not in {"RuntimeDefault", "Localhost"}:
        fautes.append("pod.seccompProfile")
    for conteneur in spec.get("containers", []) + spec.get("initContainers", []):
        contexte = conteneur.get("securityContext") or {}
        if contexte.get("allowPrivilegeEscalation") is not False:
            fautes.append(f"{conteneur['name']}.allowPrivilegeEscalation")
        if "ALL" not in ((contexte.get("capabilities") or {}).get("drop") or []):
            fautes.append(f"{conteneur['name']}.capabilities.drop")
    return fautes


@pytest.mark.parametrize("env", ENVIRONNEMENTS)
def test_chaque_pod_du_chart_passe_la_regle_non_root(env: str) -> None:
    fautifs = {nom: manquements(spec) for nom, spec in _modeles_de_pod(_rendu(env))}
    assert {nom: f for nom, f in fautifs.items() if f} == {}, (
        f"{env}: the Kyverno rule would refuse these pods"
    )


def test_la_regle_kyverno_est_bien_stricte() -> None:
    """The file itself: no more conditional anchor on the fields that matter."""
    politique = yaml.safe_load((RACINE / "infra" / "policies" / "pod-security.yaml").read_text())
    regle = next(r for r in politique["spec"]["rules"] if r["name"] == "non-root")
    motif = regle["validate"]["pattern"]["spec"]
    assert motif["securityContext"]["runAsNonRoot"] is True, "`=(securityContext)` laissait passer un pod nu"
    assert "seccompProfile" in motif["securityContext"]
    assert motif["containers"][0]["securityContext"]["allowPrivilegeEscalation"] is False
