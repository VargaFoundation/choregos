# SPDX-License-Identifier: Apache-2.0
"""`docs/procedures/` is generated from the procedures that ship, and drift is refused (ADR 0067)."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest
from choregos_core import procedures as core

ROOT = pathlib.Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "export_procedures_docs", ROOT / "tools/procedures/export_docs.py"
)
assert _SPEC and _SPEC.loader
export_docs = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = export_docs
_SPEC.loader.exec_module(export_docs)


def test_the_committed_docs_are_what_the_procedures_render_to() -> None:
    assert export_docs.differences() == [], "run `make procedures` and commit docs/procedures/"


def test_the_export_is_deterministic(tmp_path: pathlib.Path) -> None:
    export_docs.write(tmp_path)
    first = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.md")}
    export_docs.write(tmp_path)
    assert {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.md")} == first
    assert set(first) == {f"{pid}.md" for pid in core.load().procedures} | {"README.md"}


def test_check_fails_on_a_hand_edited_page_a_missing_page_and_a_stray_one(tmp_path: pathlib.Path) -> None:
    export_docs.write(tmp_path)
    assert export_docs.differences(tmp_path) == []
    page = tmp_path / "connector.tracker.jira.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nA hand edit.\n", encoding="utf-8")
    (tmp_path / "connector.notify.slack.md").unlink()
    (tmp_path / "connector.stray.md").write_text("stray", encoding="utf-8")
    problems = export_docs.differences(tmp_path)
    assert any("connector.tracker.jira.md: differs" in p for p in problems)
    assert any("connector.notify.slack.md: missing" in p for p in problems)
    assert any("connector.stray.md: no procedure" in p for p in problems)


def test_the_command_line_check_exits_non_zero_on_drift() -> None:
    ok = subprocess.run(
        [sys.executable, str(ROOT / "tools/procedures/export_docs.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0, ok.stderr
    page = ROOT / "docs/procedures/source.web.md"
    original = page.read_text(encoding="utf-8")
    try:
        page.write_text(original + "\nedited\n", encoding="utf-8")
        bad = subprocess.run(
            [sys.executable, str(ROOT / "tools/procedures/export_docs.py"), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert bad.returncode == 1
        assert "source.web.md" in bad.stderr
    finally:
        page.write_text(original, encoding="utf-8")


def test_the_docs_show_placeholders_not_a_deployments_address() -> None:
    page = (ROOT / "docs/procedures/connector.tracker.jira.md").read_text(encoding="utf-8")
    assert "<api_url>/api/v1/webhooks/jira" in page
    assert "{{" not in page


@pytest.mark.parametrize("name", ["--mocks"])
def test_the_mock_data_has_the_apis_shape(tmp_path: pathlib.Path, name: str) -> None:
    target = tmp_path / "procedures.json"
    assert export_docs.main([name, str(target)]) == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert len(data) == len(core.load().procedures)
    jira = next(p for p in data if p["id"] == "connector.tracker.jira")
    assert jira["values"] == {"webhook_url:jira": "https://choregos.example.com/api/v1/webhooks/jira"}
    assert {"id", "title", "version", "audience", "applies_to", "link", "reviewed", "sections"} <= set(jira)
