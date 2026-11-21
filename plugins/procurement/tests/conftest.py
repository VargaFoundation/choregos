# SPDX-License-Identifier: Apache-2.0
"""A job workspace with the fixtures handed as inputs, as the runner mounts them (ADR 0201)."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from tranibor_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    JobSpec,
    ModelRef,
    PlaybookRef,
    ProjectRef,
    StageInput,
    TransitionRef,
    WorkItemRef,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Every fixture, and what a reader of it must find: (file, readable, type, pages, notation, reason).
EXPECTED = [
    ("alderbrook-offer.pdf", True, "pdf", 1, "decimal_point", ""),
    ("brightfield-offer.docx", True, "docx", None, "decimal_comma", ""),
    ("corvane-prices.xlsx", True, "xlsx", None, "decimal_point", ""),
    ("corvane-injected.pdf", True, "pdf", 1, "decimal_point", ""),
    ("alderbrook-lines.csv", True, "csv", None, "decimal_point", ""),
    ("scan-only.pdf", False, "", None, "", "no_text_layer"),
    ("locked-offer.pdf", False, "", None, "", "encrypted"),
]


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty job workspace: `.choregos/inputs/` exists, and `TRANIBOR_WORKSPACE` names it."""
    root = tmp_path / "ws"
    (root / ".choregos" / "inputs").mkdir(parents=True)
    monkeypatch.setenv("TRANIBOR_WORKSPACE", str(root))
    return root


@pytest.fixture
def hand(workspace: Path) -> Callable[..., Path]:
    """Hands fixtures to the step: `hand("a.pdf", "b.docx")`; no name hands all of them."""

    def _hand(*names: str) -> Path:
        for name in names or sorted(p.name for p in FIXTURES.iterdir()):
            shutil.copyfile(FIXTURES / name, workspace / ".choregos" / "inputs" / name)
        return workspace

    return _hand


@pytest.fixture
def stage_input() -> StageInput:
    """The step as the runner would receive it: a job that declares the inline output `offers_text`."""
    return StageInput(
        run_id="run-test-1",
        attempt=1,
        project=ProjectRef(slug="demo", org="acme", test_command="", lint_command="", typecheck_command=""),
        work_item=WorkItemRef(key="acme/demo#1", title="Sourcing", body="Three offers."),
        transition=TransitionRef(
            id="t-extract",
            role="extract",
            **{"from": "ready"},
            to="extracted",
            outputs=["offers_text"],
            output_types={"offers_text": "json"},
        ),
        repo=None,
        agent=AgentRef(backend="job"),
        model=ModelRef(litellm_model="none", base_url=""),
        gateway_key=None,
        playbook=PlaybookRef(ref="extract@test", prompt="Extract."),
        job=JobSpec(command=[sys.executable, "-m", "tranibor_procurement.cli", "extract"]),
        budget=Budget(usd=1, max_turns=1, max_minutes=5),
        callbacks=Callbacks(api_url="http://localhost:0/internal", run_token="tok"),
    )
