"""The `dod.facts` example of `DodConfig` means what it says (C3).

The example used to read `collector | grep -qv KEY` for "the finding is resolved". `grep -v` keeps the
lines WITHOUT the key, so the command succeeded as soon as one line of the collector's output lacked it:
a finding still reported read as resolved. A project copies this example into its configuration, and a
measured fact is what `evidence_facts` trusts, so the example is run here, verbatim, the way the runner
runs a fact (`run_command`: a shell, the workspace as the working directory, exit code 0 = true).
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_contracts.project import DodConfig

#: The stand-in collector the example calls, relative to the workspace.
COLLECTOR = "ops/collectors/20-k3s.sh"
KEY = "FINDING-KEY"


def _example() -> dict[str, Any]:
    """The `dod:` block of the docstring, parsed as a project's configuration would be."""
    doc = DodConfig.__doc__ or ""
    start = doc.index("    dod:")
    block = doc[start:].split("\n\n", 1)[0]
    parsed = yaml.safe_load(textwrap.dedent(block))
    DodConfig.model_validate(parsed["dod"])
    return dict(parsed["dod"]["facts"])


def _measure(command: str, workspace: Path) -> bool:
    """A fact as the runner measures it: the command in a shell, in the workspace, exit 0 = true."""
    done = subprocess.run(command, shell=True, cwd=workspace, capture_output=True, check=False)
    return done.returncode == 0


def _collector(workspace: Path, output: str, exit_code: int = 0) -> None:
    script = workspace / COLLECTOR
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"#!/bin/sh\nprintf '{output}'\nexit {exit_code}\n", encoding="utf-8")
    script.chmod(0o755)


@pytest.fixture
def finding_resolved() -> str:
    facts = _example()
    assert "finding_resolved" in facts, facts
    assert COLLECTOR in facts["finding_resolved"] and KEY in facts["finding_resolved"]
    return str(facts["finding_resolved"])


def test_a_finding_still_reported_is_not_resolved(finding_resolved: str, tmp_path: Path) -> None:
    _collector(tmp_path, f"ok: disk\\n{KEY}: kernel 6.1 is end of life\\nok: certificates\\n")
    assert _measure(finding_resolved, tmp_path) is False


def test_a_finding_no_longer_reported_is_resolved(finding_resolved: str, tmp_path: Path) -> None:
    _collector(tmp_path, "ok: disk\\nok: kernel\\nok: certificates\\n")
    assert _measure(finding_resolved, tmp_path) is True


def test_a_collector_that_fails_or_is_missing_proves_nothing(finding_resolved: str, tmp_path: Path) -> None:
    """The fact is false, never "resolved", when the collector did not run: the same choice as for a
    command that does not exist."""
    assert _measure(finding_resolved, tmp_path) is False, "no collector at all"
    _collector(tmp_path, "", exit_code=2)
    assert _measure(finding_resolved, tmp_path) is False, "a collector that failed"


def test_the_former_example_said_resolved_while_the_key_was_there(tmp_path: Path) -> None:
    """Why the example changed: `grep -qv` succeeds whenever ANY line lacks the key."""
    _collector(tmp_path, f"ok: disk\\n{KEY}: kernel 6.1 is end of life\\n")
    assert _measure(f"{COLLECTOR} | grep -qv {KEY}", tmp_path) is True


def test_the_identity_document_example_reads_a_file(tmp_path: Path) -> None:
    command = str(_example()["identity_document"])
    assert _measure(command, tmp_path) is False
    (tmp_path / "case").mkdir()
    (tmp_path / "case" / "identity.pdf").write_bytes(b"%PDF-1.7\n")
    assert _measure(command, tmp_path) is True
