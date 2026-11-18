"""The product does not name the Varga Foundation (ADR 0186).

The publisher is GRAAL.SYSTEMS SAS. A tracked file that spells "Varga" outside the allowlist below fails:
a copyright line, a footer, a contact address, a default source or an example that names the foundation
would bring it back. Each exception carries its reason.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
VARGA = re.compile("varga", re.IGNORECASE)

#: Prefixes: history the product does not rewrite.
HISTORY = (
    "docs/adr/",  # the decisions, past tense, and the amendments that retire them
    "docs/plan/",  # the plan's rows say what was done when
    "apps/api/src/choregos_api/migrations/versions/",  # a migration means what it meant when it ran
    "trials/",  # dated reports
    "plugins/offline/",  # the archives and the lock record the index and identity that signed them
)

#: Files: the frozen namespace `ghcr.io/vargafoundation` and the old source name, until republished.
ALLOWED = {
    "tests/licences/test_no_varga.py": "this guard",
    "packages/core/src/choregos_core/plugins/sources.py": "resolves the old default source name",
    "packages/core/tests/test_plugin_sources.py": "tests that the old default source name resolves",
    "apps/api/tests/test_default_plugin_source_migration.py": "tests the rename of the old source name",
    "infra/policies/images.yaml": "admits the images published before the move",
    "infra/policies/plugin-servers.yaml": "admits the plugin servers published before the move",
    "infra/bootstrap/gvisor.yaml": "the gVisor installer image exists only in the frozen namespace",
    ".github/workflows/code-agent-examples.yml": "the runner image exists only in the frozen namespace",
    "examples/agents/langgraph/Dockerfile": "the runner image exists only in the frozen namespace",
    "examples/agents/openai-agents/Dockerfile": "the runner image exists only in the frozen namespace",
    "examples/agents/plain/Dockerfile": "the runner image exists only in the frozen namespace",
    "examples/agents/pydantic-ai/Dockerfile": "the runner image exists only in the frozen namespace",
    "tests/tools/test_publishing_identity.py": "the policy keeps the frozen plugin namespace",
    "apps/api/tests/test_catalogue_provenance_migration.py": "an old migration records the old source name",
}


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [name for name in out.decode().split("\0") if name]


def _holds_varga(name: str) -> bool:
    try:
        return bool(VARGA.search((ROOT / name).read_text(encoding="utf-8")))
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return False


def test_varga_is_named_only_where_the_allowlist_says() -> None:
    offenders = [
        name
        for name in _tracked()
        if not name.startswith(HISTORY) and name not in ALLOWED and _holds_varga(name)
    ]
    assert offenders == [], (
        "the product does not name the Varga Foundation (ADR 0186): "
        + ", ".join(offenders)
        + "; name GRAAL.SYSTEMS SAS, or add the file to ALLOWED with its reason"
    )


def test_every_exception_is_still_needed() -> None:
    tracked = set(_tracked())
    stale = [
        name
        for name in ALLOWED
        if name != "tests/licences/test_no_varga.py" and (name not in tracked or not _holds_varga(name))
    ]
    assert stale == [], "no longer names Varga, remove from ALLOWED: " + ", ".join(stale)


def test_the_guard_sees_a_file_that_names_the_foundation(tmp_path: pathlib.Path) -> None:
    """Red without the check: the pattern matches the spellings the product used."""
    for text in ("Varga Foundation", "VargaFoundation/choregos", "docs.varga.foundation", "@varga.dev"):
        assert VARGA.search(text), text
