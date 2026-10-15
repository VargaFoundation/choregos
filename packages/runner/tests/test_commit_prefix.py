"""The commit prefix says what the step did: a documentation step commits as `docs` (S39-15)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from choregos_runner.runner import _commit_message


@pytest.mark.parametrize(
    ("role", "prefix"), [("document", "docs"), ("implement", "feat"), ("verify", "test")]
)
def test_the_prefix_follows_the_role(role: str, prefix: str) -> None:
    stage: Any = SimpleNamespace(
        transition=SimpleNamespace(role=role), work_item=SimpleNamespace(key="acme#12")
    )
    result: Any = SimpleNamespace(summary="Updated the queue page")
    assert _commit_message(stage, result) == f"{prefix}(12): Updated the queue page"
