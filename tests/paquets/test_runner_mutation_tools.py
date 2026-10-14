"""The runner image carries the mutation-testing tools the `qa` agent runs (S33-06).

An agent whose instructions say `mutmut run` in an image without mutmut is a prompt, not a QA agent:
the tools are installed at a pinned version and called at build time, so that a missing one breaks
the build instead of the first run.
"""

from __future__ import annotations

import re
from pathlib import Path

from choregos_core.catalogue_d_agents import entree

DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "runner.Dockerfile"


def test_mutmut_and_stryker_are_pinned_and_called_at_build_time() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^ARG MUTMUT_VERSION=\d+\.\d+\.\d+$", text, re.M)
    assert re.search(r"^ARG STRYKER_VERSION=\d+\.\d+\.\d+$", text, re.M)
    assert '"mutmut==${MUTMUT_VERSION}"' in text
    assert '"@stryker-mutator/core@${STRYKER_VERSION}"' in text
    assert "mutmut --version" in text and "stryker --version" in text


def test_the_qa_agent_names_the_tools_the_image_carries() -> None:
    qa = entree("qa")
    assert qa is not None
    assert {"runner:mutmut", "runner:stryker"} <= set(qa.tools)
