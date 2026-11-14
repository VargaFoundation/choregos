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

A coupling can also arrive without the name: a data platform's API key pasted in a default, or a
call to its private model endpoint instead of its MCP server. Those tokens of a data platform's
private surface are refused in the same trees, the catalogue file included: a catalogue entry may
name a data platform, it reaches that platform through its public MCP endpoint, never through its
private surface (ADR 0079 §2).
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

#: A data platform's private surface: the prefix of its API keys and the path of its model
#: endpoint. Either one in what ships is a direct coupling, whatever the name around it. No
#: exception: neither the catalogue, nor the branding, nor a wheel tag has a reason to hold them.
PRIVATE_SURFACE = re.compile(r"gk1\.|/api/v1/llm")


def _tracked_files(*, allowed: frozenset[str] = ALLOWED_FILES) -> list[pathlib.Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *TREES],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode()
    return [ROOT / name for name in listed.split("\0") if name and name not in allowed]


def _offending_lines(paths: list[pathlib.Path], pattern: re.Pattern[str]) -> list[str]:
    offenders: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                name = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                offenders.append(f"{name}:{number}: {line.strip()[:120]}")
    return offenders


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
    offenders = _offending_lines(_tracked_files(), GRAAL)
    assert not offenders, (
        "Choregos names graal outside the catalogue:\n  "
        + "\n  ".join(offenders)
        + "\nThe two products meet through MCP, A2A, webhooks and the customer's identity provider "
        "only (ADR 0079): reach graal as a catalogue connector, not from the code."
    )


def test_the_private_surface_pattern_catches_a_key_and_the_model_path() -> None:
    # Placeholders, not keys: the shape of a prefix is enough, and a secret scanner stays quiet.
    caught = (
        'default_credential = "gk1.xxxxxxxx"',
        "Authorization: Bearer gk1.xxxxxxxx",
        "https://data.example.com/api/v1/llm/chat/completions",
        'base_url: "http://platform:8080/api/v1/llm"',
    )
    spared = (
        "GET /api/v1/auth/login?reauth=1",
        "https://gateway.example.com/v1/chat/completions",
        "sha256:9c1e0gk1f00d",
        "gk1_reserved",
    )
    assert all(PRIVATE_SURFACE.search(text) for text in caught), "the pattern misses the private surface"
    assert not any(PRIVATE_SURFACE.search(text) for text in spared), "the pattern flags ordinary text"


def test_a_planted_line_of_the_private_surface_is_reported(tmp_path: pathlib.Path) -> None:
    """The scan itself, not only the pattern: a planted file is read and its line is named."""
    planted = tmp_path / "settings.py"
    planted.write_text(
        'MODEL_URL = "https://platform.example.com/api/v1/llm"\nNAME = "harmless"\nCREDENTIAL = "gk1.xxxx"\n',
        encoding="utf-8",
    )
    clean = tmp_path / "clean.py"
    clean.write_text('URL = "https://platform.example.com/mcp"\n', encoding="utf-8")
    offenders = _offending_lines([planted, clean], PRIVATE_SURFACE)
    assert [line.split(": ", 1)[0] for line in offenders] == [f"{planted}:1", f"{planted}:3"]


def test_nothing_that_ships_holds_a_data_platforms_private_surface() -> None:
    offenders = _offending_lines(_tracked_files(allowed=frozenset()), PRIVATE_SURFACE)
    assert not offenders, (
        "Choregos holds a data platform's private surface (an API key prefix or its model endpoint):\n  "
        + "\n  ".join(offenders)
        + "\nA data platform is reached through its MCP server, as a catalogue connector whose "
        "credential is sealed at install (ADR 0079 §2), never through a key or a private endpoint "
        "written in what ships."
    )
