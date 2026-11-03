"""Can a trade bring its own workflow template?

Until 2026-09-26, no: `TEMPLATE_NAMES` was frozen at three names and `template_path` refused
everything else. The only way out was to post the full YAML through `PUT /projects/{id}/workflow`,
so without a name, without reuse, and without two projects of the same house being able to start from
the same model.

It is the same seam as the playbooks (`CHOREGOS_PLAYBOOKS_DIR`), for the same reason: the
states and transitions of a trade have no place in this package, whereas the rest of the
machine (budgets, guarantees, memory) does not change from one domain to the next.

The property that matters is the third: **the deployment wins over the package**. Without it,
adapting `default-simple` to a house would mean rewriting the platform.
"""

from __future__ import annotations

import pathlib

import pytest
from choregos_core.dsl import (
    TEMPLATE_NAMES,
    forget_templates,
    load_template,
    template_names,
    template_path,
    template_yaml,
)


@pytest.fixture(autouse=True)
def clean_cache() -> None:
    forget_templates()


def _write(directory: pathlib.Path, name: str, final_state: str = "done") -> pathlib.Path:
    """A minimal but VALID workflow: the test must fail on the seam, not on the DSL."""
    path = directory / f"{name}.yaml"
    path.write_text(template_yaml("default-simple"), encoding="utf-8")
    return path


def test_without_a_setting_only_the_package_templates_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", raising=False)
    assert set(template_names()) == set(TEMPLATE_NAMES)


def test_a_deployment_template_becomes_available(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "case-review")
    monkeypatch.setenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", str(tmp_path))

    assert "case-review" in template_names()
    assert set(TEMPLATE_NAMES) <= set(template_names()), "the shipped templates stay there"
    assert load_template("case-review").states, "it must load, not only be listed"


def test_the_deployment_wins_over_the_package(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adapt a shipped template without rewriting the platform: the whole point of the seam."""
    replacement = _write(tmp_path, "default-simple")
    monkeypatch.setenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", str(tmp_path))

    assert template_path("default-simple") == replacement
    assert template_names().count("default-simple") == 1, "the name must not appear twice"


def test_several_directories_in_order(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    _write(first, "house")
    _write(second, "house")
    monkeypatch.setenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", f"{first}:{second}")
    assert template_path("house").parent == first, "the first directory named wins"


def test_an_unknown_name_says_what_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_WORKFLOW_TEMPLATES_DIR", raising=False)
    with pytest.raises(FileNotFoundError, match="default-simple"):
        template_path("this-one-does-not-exist")
