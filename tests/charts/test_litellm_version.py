"""S40-16g: the LiteLLM the product ships is out of GHSA-7hp6-4w63-5g45, in both installs.

1.91.0 up to 1.100.4 lets an `internal_user` become `proxy_admin`, then run code (critical). The
local install (`compose.yaml`) and the chart pin their own tag: both are read, and they must agree,
so that what the local install proves is what the chart deploys.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
# The live tracing stack says it runs the chart's image: what it proves must be what the chart ships.
LIVE_OTEL_COMPOSE = ROOT / "tests" / "live" / "otel" / "compose.yaml"
CHART_VALUES = ROOT / "charts" / "choregos" / "charts" / "litellm" / "values.yaml"
REPOSITORY = "ghcr.io/berriai/litellm-database"
# The first release out of GHSA-7hp6-4w63-5g45 on the main line (also fixed: 1.101.3, 1.102.2, 1.103.1).
FIRST_FIXED = (1, 100, 4)


def _version(tag: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    assert match, f"LiteLLM tag {tag!r} is not a release tag vX.Y.Z"
    return tuple(int(part) for part in match.groups())


def _compose_tag(compose: pathlib.Path = COMPOSE) -> str:
    image = yaml.safe_load(compose.read_text())["services"]["litellm"]["image"]
    repository, _, tag = image.partition(":")
    assert repository == REPOSITORY, image
    return str(tag)


def _chart_tag() -> str:
    image = yaml.safe_load(CHART_VALUES.read_text())["image"]
    assert image["repository"] == REPOSITORY, image
    return str(image["tag"])


def test_the_local_install_runs_a_litellm_out_of_the_critical_advisory() -> None:
    assert _version(_compose_tag()) >= FIRST_FIXED, "GHSA-7hp6-4w63-5g45: LiteLLM >= 1.100.4"


def test_the_chart_runs_a_litellm_out_of_the_critical_advisory() -> None:
    assert _version(_chart_tag()) >= FIRST_FIXED, "GHSA-7hp6-4w63-5g45: LiteLLM >= 1.100.4"


def test_the_local_install_and_the_chart_run_the_same_litellm() -> None:
    assert _compose_tag() == _chart_tag()


def test_the_live_tracing_stack_runs_the_charts_litellm() -> None:
    assert _compose_tag(LIVE_OTEL_COMPOSE) == _chart_tag()
