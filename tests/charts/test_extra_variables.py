"""`global.extraEnv`: a setting the chart does not know yet, without forking the chart.

To activate, for example, an extension shipped inactive. Two properties: the variable reaches ALL the
processes that read the platform's settings -- the API, each worker, the migration Job, without
which an extension activated in the API but not in the migrations would start without its tables -- and a
variable already set by the chart is refused at render time, rather than silently replaced.
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


def _porteurs(rendu: str, name: str) -> tuple[set[str], set[str]]:
    """(processes that read the settings, those among them that receive `nom`)."""
    readers: set[str] = set()
    porteurs: set[str] = set()
    for doc in yaml.safe_load_all(rendu):
        if not doc or doc.get("kind") not in {"Deployment", "Job"}:
            continue
        template = doc["spec"]["template"]["spec"]
        for conteneur in template.get("containers", []):
            names = {v["name"] for v in (conteneur.get("env") or [])}
            if "CHOREGOS_ENV" not in names:
                continue
            readers.add(doc["metadata"]["name"])
            if name in names:
                porteurs.add(doc["metadata"]["name"])
    return readers, porteurs


def test_the_variable_reaches_every_process_of_the_platform() -> None:
    rendu = _render("global.extraEnv[0].name=CHOREGOS_ESSAI_ONTOLOGIE", "global.extraEnv[0].value=1")
    assert rendu.returncode == 0, rendu.stderr[-400:]
    readers, porteurs = _porteurs(rendu.stdout, "CHOREGOS_ESSAI_ONTOLOGIE")
    assert any("migration" in name for name in readers), f"the migration Job is not seen: {readers}"
    assert porteurs == readers, f"sans la variable : {sorted(readers - porteurs)}"
    assert 'value: "1"' in rendu.stdout


def test_without_a_value_nothing_is_added() -> None:
    rendu = _render()
    assert rendu.returncode == 0, rendu.stderr[-400:]
    assert "CHOREGOS_ESSAI_ONTOLOGIE" not in rendu.stdout


def test_a_variable_already_set_by_the_chart_is_refused() -> None:
    rendu = _render(
        "global.extraEnv[0].name=CHOREGOS_DATABASE_URL", "global.extraEnv[0].value=postgresql://x"
    )
    assert rendu.returncode != 0, "the chart rendered two values for CHOREGOS_DATABASE_URL"
    assert "CHOREGOS_DATABASE_URL" in rendu.stderr


def test_the_env_reference_pattern_reaches_every_process_when_set() -> None:
    """S39-06b: `global.secrets.envPattern` is the allowlist of `env:NAME` references; unset, the
    code's default (`^CONNECTOR_…`) applies and nothing is rendered."""
    rendu = _render("global.secrets.envPattern=^(CONNECTOR_|JIRA_)[A-Z0-9_]*$")
    assert rendu.returncode == 0, rendu.stderr[-400:]
    readers, porteurs = _porteurs(rendu.stdout, "CHOREGOS_SECRET_ENV_PATTERN")
    assert readers and porteurs == readers, f"without the variable: {sorted(readers - porteurs)}"
    assert "CHOREGOS_SECRET_ENV_PATTERN" not in _render().stdout


def _secret_ref(rendu: str, name: str) -> list[dict[str, object]]:
    """The `valueFrom` of every `nom` entry of a container that reads the platform's settings."""
    refs: list[dict[str, object]] = []
    for doc in yaml.safe_load_all(rendu):
        if not doc or doc.get("kind") not in {"Deployment", "Job"}:
            continue
        for conteneur in doc["spec"]["template"]["spec"].get("containers", []):
            for v in conteneur.get("env") or []:
                if v["name"] == name:
                    refs.append(v.get("valueFrom") or {"value": v.get("value")})
    return refs


def test_a_secret_is_mapped_by_value_from_into_every_process() -> None:
    """S38-18: a connector key (`env:CONNECTOR_…`) comes from a Secret. The chart rendered only
    `value`, so `valueFrom` gave every pod an EMPTY key without anything saying so."""
    rendu = _render(
        "global.extraEnv[0].name=CONNECTOR_ALPHAVANTAGE_API_KEY",
        "global.extraEnv[0].valueFrom.secretKeyRef.name=choregos-app-secrets",
        "global.extraEnv[0].valueFrom.secretKeyRef.key=CONNECTOR_ALPHAVANTAGE_API_KEY",
        "global.extraEnv[0].valueFrom.secretKeyRef.optional=true",
    )
    assert rendu.returncode == 0, rendu.stderr[-400:]
    readers, porteurs = _porteurs(rendu.stdout, "CONNECTOR_ALPHAVANTAGE_API_KEY")
    assert readers and porteurs == readers, f"without the variable: {sorted(readers - porteurs)}"
    expected = {
        "secretKeyRef": {
            "name": "choregos-app-secrets",
            "key": "CONNECTOR_ALPHAVANTAGE_API_KEY",
            "optional": True,
        }
    }
    refs = _secret_ref(rendu.stdout, "CONNECTOR_ALPHAVANTAGE_API_KEY")
    assert refs and all(r == expected for r in refs), refs


@pytest.mark.parametrize(
    "extra",
    [
        (),
        ("global.extraEnv[0].value=x", "global.extraEnv[0].valueFrom.secretKeyRef.name=s"),
    ],
    ids=["neither", "both"],
)
def test_an_entry_needs_exactly_one_of_value_or_value_from(extra: tuple[str, ...]) -> None:
    rendu = _render("global.extraEnv[0].name=CONNECTOR_X", *extra)
    assert rendu.returncode != 0, "the chart rendered an extraEnv entry it cannot fill"
    assert "exactly one of value or valueFrom" in rendu.stderr
