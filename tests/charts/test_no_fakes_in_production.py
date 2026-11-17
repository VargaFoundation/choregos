"""The fakes are refused in staging and prod, at the render (S80-03).

`global.fakes` becomes `CHOREGOS_FAKES` in every pod (`choregos.commonEnv`): on, the egress control
only warns and the API's secret store can be an in-memory fake. The processes refuse to start on it in
staging and prod; the render says it before anything is deployed, and names the value. A bench
(`values/dev.yaml`, `values/local.yaml`, the defaults) keeps rendering with it.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
PRESETS = CHART / "values"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendered(
    preset: str | None, *sets: str, strings: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    if preset is not None:
        command += ["-f", str(PRESETS / f"{preset}.yaml")]
    for value in sets:
        command += ["--set", value]
    for value in strings:
        command += ["--set-string", value]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _fakes_of_the_pods(stdout: str) -> set[str]:
    """Every value `CHOREGOS_FAKES` takes in a rendered container, wherever it is declared."""
    found: set[str] = set()
    stack: list[object] = [document for document in yaml.safe_load_all(stdout) if document]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get("name") == "CHOREGOS_FAKES" and "value" in item:
                found.add(str(item["value"]))
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


@pytest.mark.parametrize("preset", ["prod", "staging"])
def test_the_production_presets_with_the_fakes_on_fail_to_render(preset: str) -> None:
    rendu = _rendered(preset, "global.fakes=true")
    assert rendu.returncode != 0
    assert "global.fakes" in rendu.stderr and f"global.environment={preset}" in rendu.stderr


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_a_string_that_turns_the_fakes_on_is_refused_too(value: str) -> None:
    """The processes read `"1"` or `"yes"` as on: the render does as well."""
    rendu = _rendered("prod", strings=(f"global.fakes={value}",))
    assert rendu.returncode != 0 and "global.fakes" in rendu.stderr


def test_any_environment_named_prod_refuses_them_whatever_preset_it_starts_from() -> None:
    """`values/dev.yaml` turns the fakes on: retargeting it at prod must not carry them there."""
    rendu = _rendered("dev", "global.environment=prod")
    assert rendu.returncode != 0 and "global.fakes" in rendu.stderr


def test_the_fakes_handed_to_every_run_are_refused_in_production() -> None:
    """`choregos-orchestrator.runner.env` reaches every runner (pod env and `CHOREGOS_RUNNER_ENV`)."""
    rendu = _rendered("prod", strings=("choregos-orchestrator.runner.env.CHOREGOS_FAKES=1",))
    assert rendu.returncode != 0
    assert "choregos-orchestrator.runner.env.CHOREGOS_FAKES" in rendu.stderr


@pytest.mark.parametrize("preset", ["prod", "staging"])
def test_the_production_presets_render_with_the_fakes_off(preset: str) -> None:
    rendu = _rendered(preset)
    assert rendu.returncode == 0, rendu.stderr
    assert _fakes_of_the_pods(rendu.stdout) == {"false"}


@pytest.mark.parametrize("preset", ["dev", "local", None])
def test_a_bench_keeps_its_fakes(preset: str | None) -> None:
    rendu = _rendered(preset, "global.fakes=true")
    assert rendu.returncode == 0, rendu.stderr
    assert _fakes_of_the_pods(rendu.stdout) == {"true"}
