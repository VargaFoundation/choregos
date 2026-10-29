"""The map's secondary edges are labelled in English by the engine (ADR 0046, S34-03).

The console still translates the former French labels, as a fallback for one release
(`apps/web/src/components/workflows/carte/disposition.ts`). This test holds the engine side, so
that the fallback can be removed without a map going back to French.
"""

from __future__ import annotations

import re

import pytest
from choregos_core.dsl import TEMPLATE_NAMES, load_template, to_graph

#: Every label a secondary edge may carry; `on failure (≤N)` is read by the console's pattern.
ENGLISH_LABELS = {
    "if rejected",
    "when retries run out",
    "on changes requested",
    "when reviews run out",
    "question",
    "budget exceeded",
    "timeout",
    "abandon",
}
_ON_FAILURE = re.compile(r"^on failure \(≤\d+\)$")


@pytest.mark.parametrize(
    "name", [n for n in TEMPLATE_NAMES if n != "blank"]
)  # a skeleton has no secondary edge
def test_secondary_edge_labels_are_english(name: str) -> None:
    labels = {e["label"] for e in to_graph(load_template(name))["edges"] if e["kind"] != "nominal"}
    assert labels, "a template without a secondary edge proves nothing"
    unknown = {label for label in labels if label not in ENGLISH_LABELS and not _ON_FAILURE.match(label)}
    assert unknown == set()


def test_the_advanced_template_carries_every_kind_of_label() -> None:
    labels = {e["label"] for e in to_graph(load_template("advanced"))["edges"] if e["kind"] != "nominal"}
    assert labels >= ENGLISH_LABELS
    assert any(_ON_FAILURE.match(label) for label in labels)
