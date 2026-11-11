"""What the native CLI binary relies on, proven on the Python package (S62-07).

- `choregos --version` prints the release's version (the binary's smoke test checks it too);
- the CLI imports no OpenTelemetry, whatever it runs: OpenTelemetry is the `telemetry` extra of
  `choregos-core`, which only the services that emit spans ask for;
- every workspace package that imports `choregos_core.telemetry`, `choregos_core.llm_spans` or
  `opentelemetry` declares that extra, and the CLI declares none of it.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

from choregos_cli import __version__
from choregos_cli.main import app
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "packages" / "core" / "src" / "choregos_core" / "dsl" / "templates" / "default-simple.yaml"
USES_TELEMETRY = re.compile(
    r"^\s*(from choregos_core(\.telemetry|\.llm_spans)? import .*\b(telemetry|llm_spans)\b"
    r"|from choregos_core\.(telemetry|llm_spans) import"
    r"|import opentelemetry|from opentelemetry)",
    re.M,
)


def _pyproject(directory: Path) -> dict:  # type: ignore[type-arg]
    return tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))


def test_version_prints_the_release_version() -> None:
    expected = _pyproject(ROOT / "packages" / "cli")["project"]["version"]
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"choregos {expected}"
    assert __version__ == expected


def test_the_cli_imports_no_opentelemetry() -> None:
    """A fresh interpreter runs the CLI's offline commands; no `opentelemetry` module gets loaded."""
    script = textwrap.dedent(
        f"""
        import sys
        from typer.testing import CliRunner
        from choregos_cli.main import app

        runner = CliRunner()
        for args in (["--version"], ["workflow", "templates"], ["workflow", "validate", {str(TEMPLATE)!r}],
                     ["workflow", "show", {str(TEMPLATE)!r}, "--mermaid"], ["plugin", "--help"]):
            result = runner.invoke(app, args)
            assert result.exit_code == 0, (args, result.output)
        loaded = sorted(m for m in sys.modules if m.split(".")[0] == "opentelemetry")
        assert not loaded, loaded
        print("clean")
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean"


def test_core_keeps_opentelemetry_in_its_telemetry_extra_only() -> None:
    project = _pyproject(ROOT / "packages" / "core")["project"]
    assert not [d for d in project["dependencies"] if d.startswith("opentelemetry")]
    assert {d.split("==")[0] for d in project["optional-dependencies"]["telemetry"]} == {
        "opentelemetry-sdk",
        "opentelemetry-exporter-otlp-proto-http",
    }


def test_every_package_that_traces_declares_the_extra_and_the_cli_does_not() -> None:
    members = [
        *sorted((ROOT / "packages").glob("*/pyproject.toml")),
        *sorted((ROOT / "apps").glob("*/pyproject.toml")),
    ]
    tracing: set[str] = set()
    for pyproject in members:
        directory = pyproject.parent
        if directory.name == "core":
            continue
        sources = directory / "src"
        if any(USES_TELEMETRY.search(p.read_text(encoding="utf-8")) for p in sources.rglob("*.py")):
            tracing.add(directory.name)
            dependencies = _pyproject(directory)["project"]["dependencies"]
            assert "choregos-core[telemetry]" in dependencies, f"{directory.name} traces without the extra"
    assert {"api", "orchestrator", "runner"} <= tracing, tracing
    assert "cli" not in tracing
    assert "choregos-core[telemetry]" not in _pyproject(ROOT / "packages" / "cli")["project"]["dependencies"]
