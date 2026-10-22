"""S41-16: the Kubernetes version a hosted agent's native sidecar needs, as the API server says it."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core.hosted import kubernetes_version, native_sidecars_refusal


@pytest.mark.parametrize(
    ("version", "parsed"),
    [
        ("v1.29.3-eks-abc", (1, 29)),
        ("1.30+", (1, 30)),
        ("v1.28.15-gke.1200", (1, 28)),
        (" v1.31.0 ", (1, 31)),
        ("v1.9", (1, 9)),
        ({"gitVersion": "v1.29.3-eks-abc"}, (1, 29)),
        ({"major": "1", "minor": "30+", "gitVersion": "v1.30.4-gke.1"}, (1, 30)),
        ({"major": "1", "minor": "30+"}, (1, 30)),
        ({"gitVersion": "", "major": "1", "minor": "27"}, (1, 27)),
        ("", None),
        ("latest", None),
        (None, None),
        ({}, None),
        ({"major": "1"}, None),
    ],
)
def test_the_version_is_read_from_every_form_a_cluster_writes(version: Any, parsed: Any) -> None:
    assert kubernetes_version(version) == parsed


def test_1_29_and_later_run_a_native_sidecar() -> None:
    assert native_sidecars_refusal("v1.29.0") is None
    assert native_sidecars_refusal("1.30+") is None
    assert native_sidecars_refusal("v2.0.0") is None


def test_1_28_is_refused_naming_the_error_and_both_versions() -> None:
    refusal = native_sidecars_refusal("v1.28.9")
    assert refusal is not None and refusal.startswith("hosted_unsupported_cluster:")
    assert "1.29 or later" in refusal and "runs 1.28" in refusal
    assert native_sidecars_refusal("v1.9.0") is not None, "compared as numbers, not as text"


def test_a_version_that_cannot_be_read_is_refused() -> None:
    refusal = native_sidecars_refusal({"gitVersion": "weird"})
    assert refusal is not None and "`weird` cannot be read" in refusal
