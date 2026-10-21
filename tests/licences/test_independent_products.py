"""Does Choregos still run without graal?

[ADR 0079](../../docs/adr/0079-choregos-and-graal-are-autonomous-products.md): Choregos and graal
are two autonomous products. They meet through public interfaces only (MCP, A2A, webhooks, the
customer's identity provider), and each installs, runs and is bought alone.

A dependency would not arrive as an `import`: graal is Java, there is nothing to import. It would
arrive as a `GRAAL_API_URL` in the configuration, a `graal` client in `packages/adapters`, a host name
in a chart or a template that only works against graal. So the test reads every tracked file of the
code, charts, templates, demo and deployment trees for the name itself, and allows three things:

  * the MCP catalogue file: a data platform's entry there is optional data (S14-01);
  * the enterprise edition's branding (GraalSystems publishes it, ADR 0024): a name, not a runtime;
  * the `graalpy` wheel tags of third-party packages.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: What ships or runs: code, charts, templates, the demo, the deployment and CI files.
TREES = (
    "packages",
    "apps",
    "templates",
    "charts",
    "infra",
    "examples",
    "tools",
    "demo",
    "docker",
    "dev",
    "integrations",
    ".github",
    "compose.yaml",
    "Makefile",
    "pyproject.toml",
    "package.json",
    "justfile",
)

#: Optional data: a catalogue entry for a data platform does not make Choregos need it.
ALLOWED_FILES = frozenset({"packages/core/src/choregos_core/mcp_catalogue/catalogue.yaml"})

#: The name, unless it is a wheel tag, the edition's publisher or the edition's product name.
GRAAL = re.compile(r"graal(?!py|systems|\s+agentic\s+platform)", re.IGNORECASE)


def _tracked_files() -> list[pathlib.Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *TREES],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode()
    return [ROOT / name for name in listed.split("\0") if name and name not in ALLOWED_FILES]


def test_there_is_code_to_check() -> None:
    """Without this guard, a moved tree would make the suite green and empty."""
    files = _tracked_files()
    assert len(files) > 1000, f"only {len(files)} files found: the trees have moved"


def test_the_pattern_catches_a_dependency_and_spares_the_branding() -> None:
    caught = (
        "GRAAL_API_URL",
        "https://api.graal.systems/v1",
        "from choregos_adapters.graal import GraalClient",
        "graal-mcp:8080",
    )
    spared = (
        "jiter-0.17.0-graalpy312-graalpy250_312_native",
        'publisher: "GraalSystems"',
        "Graal Agentic Platform · Choregos Enterprise edition",
    )
    assert all(GRAAL.search(text) for text in caught), "the pattern no longer sees graal"
    assert not any(GRAAL.search(text) for text in spared), "the pattern flags the branding"


def test_nothing_that_ships_names_graal() -> None:
    offenders: list[str] = []
    for path in _tracked_files():
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if GRAAL.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:120]}")
    assert not offenders, (
        "Choregos names graal outside the catalogue:\n  "
        + "\n  ".join(offenders)
        + "\nThe two products meet through MCP, A2A, webhooks and the customer's identity provider "
        "only (ADR 0079): reach graal as a catalogue connector, not from the code."
    )
