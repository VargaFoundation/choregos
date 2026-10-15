"""`docs_updated`: the work item's branch updates its documentation when it changes anything else (S39-15)."""

from __future__ import annotations

from typing import Any

from choregos_core import GateContext, evaluate, known_gates
from choregos_core.gates import gate_needs


def _ctx(changed: list[str], added: dict[str, str] | None = None, **kw: Any) -> GateContext:
    return GateContext(
        changed_files=changed,
        added_text=added if added is not None else dict.fromkeys(changed, "text"),
        **kw,
    )


def test_code_and_a_documentation_page_pass() -> None:
    out = evaluate("docs_updated", _ctx(["src/a.py", "docs/a.md"]))
    assert out.passed and "docs/a.md" in out.detail


def test_code_alone_fails_and_names_the_code() -> None:
    out = evaluate("docs_updated", _ctx(["src/a.py"]))
    assert not out.passed
    assert out.annotations == ["src/a.py"]
    assert "0 documentation file(s) updated" in out.detail


def test_tests_and_lockfiles_need_no_documentation() -> None:
    out = evaluate("docs_updated", _ctx(["tests/test_a.py", "uv.lock", ".github/workflows/ci.yml"]))
    assert out.passed and "nothing outside documentation and tests" in out.detail


def test_without_a_diff_the_gate_refuses() -> None:
    out = evaluate("docs_updated", _ctx(["src/a.py", "docs/a.md"], diff_available=False))
    assert not out.passed and "diff unavailable" in out.detail


def test_a_deleted_page_does_not_count_but_a_page_without_patch_does() -> None:
    deleted = _ctx(["src/a.py", "docs/old.md"], {"src/a.py": "x", "docs/old.md": ""})
    assert not evaluate("docs_updated", deleted).passed
    no_patch = _ctx(["src/a.py", "docs/old.md"], {"src/a.py": "x"})
    assert evaluate("docs_updated", no_patch).passed


def test_a_changelog_line_is_enough() -> None:
    assert evaluate("docs_updated", _ctx(["src/a.py", "CHANGELOG.md"])).passed


def test_custom_docs_and_min_files() -> None:
    params = {"docs": ["handbook/**"], "min_files": 2}
    assert not evaluate("docs_updated", _ctx(["src/a.py", "handbook/a.md"]), params).passed
    assert evaluate("docs_updated", _ctx(["src/a.py", "handbook/a.md", "handbook/b.md"]), params).passed
    # `docs/a.md` is no longer documentation once `docs` is overridden.
    assert not evaluate("docs_updated", _ctx(["src/a.py", "docs/a.md"]), params).passed


def test_the_gate_is_registered_and_reads_the_scm() -> None:
    assert "docs_updated" in known_gates()
    assert gate_needs("docs_updated") == {"scm"}
