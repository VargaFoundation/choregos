"""S80-07d: the chart's embedded memory runs the Ecphoria image the local install runs.

The move of the publication to graalsystems (S73-01) rewrote the chart's Ecphoria repository to the
publishing registry, where the memory edition was never published: every embedded
memory (the single-node profile among them) stayed in `ImagePullBackOff`, found by the first real
run of `tools/single-node/proof.sh`. `compose.yaml` kept the frozen address that serves it. Both
are read here and must agree, so that what the local install proves is what the chart deploys.
"""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART_VALUES = ROOT / "charts" / "choregos" / "charts" / "ecphoria" / "values.yaml"
COMPOSES = (ROOT / "compose.yaml", ROOT / "dev" / "compose.yaml")


def _compose_image(compose: pathlib.Path) -> str:
    image = yaml.safe_load(compose.read_text())["services"]["ecphoria"]["image"]
    # `name:tag@sha256:<digest>` (S80-04): the reference without its digest is what both agree on.
    return str(image).split("@", 1)[0]


def _chart_image() -> str:
    image = yaml.safe_load(CHART_VALUES.read_text())["image"]
    return f"{image['repository']}:{image['tag']}"


def test_the_chart_and_the_local_installs_run_the_same_ecphoria() -> None:
    for compose in COMPOSES:
        assert _chart_image() == _compose_image(compose), compose.relative_to(ROOT)


def test_the_index_is_as_wide_as_the_embedding_model_vectors() -> None:
    """Without `ECPHORIA_EMBEDDING__DIMENSION` Ecphoria sized its index at 768 and the gateway's
    `platform/embed` (text-embedding-3-small) answered 1536: every write was refused ("embedding
    dimension mismatch: expected 768, got 1536") and no memory was indexed (prod, 2026-11-18)."""
    values = yaml.safe_load(CHART_VALUES.read_text())
    assert values["embedding"]["dimension"] == 1536
    template = (CHART_VALUES.parent / "templates" / "deployment.yaml").read_text()
    assert "ECPHORIA_EMBEDDING__DIMENSION" in template
    assert "ECPHORIA_MEMORY__SEMANTIC__DEFAULT_DIMENSION" in template
