"""The reader sees Tranibor (ADR 0176, round R1): the documentation, the console, the CLI.

Round R1 renames the visible name and nothing else: Python packages, environment variables, image
names, headers and every stored value keep "choregos" (ADR 0176 points 3 to 6). These tests turn red
if the visible name goes back, and a prose file that mentions the old name again must be listed here
with its reason.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import tomllib

from typer.testing import CliRunner

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The old name as a word of prose. A part of an identifier (`choregos_core`, `X-Choregos-Secret`,
#: `ChoregosError`) is not it: those are the next rounds' and the wire's, not prose.
OLD_NAME = re.compile(r"(?<![A-Za-z0-9_\-/.])Choregos(?![A-Za-z0-9_\-/])")

#: The only lines of prose that may still say it, and why. A line that matches none of these is the
#: old name leaking back into a page.
ALLOWED_LINES = (
    # The former name is stated where the new one is introduced (README, NOTICE, trademark page).
    re.compile(r"(formerly|named|Formerly) Choregos"),
    re.compile(r"^Choregos until \d{4}-\d{2}-\d{2}"),
    # What a third-party site sees: the User-Agent and the robots.txt token keep the old name
    # until a second one can be added beside it (ADR 0176 point 6).
    re.compile(r"User-Agent|robots\.txt"),
)

#: Prose that ships or is read: not the ADRs, the plan or the French archive, which say what was true.
PROSE_ROOTS = ("README.md", "NOTICE", "SECURITY.md", "docs/", "packages/core/src/choregos_core/procedures/")
HISTORY = ("docs/adr/", "docs/plan/", "docs/fr/")


def _tracked_prose() -> list[str]:
    names = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    return [
        n
        for n in names
        if n == "NOTICE"
        or (n.startswith(PROSE_ROOTS) and not n.startswith(HISTORY) and n.endswith((".md", ".mdx")))
    ]


def test_the_prose_does_not_say_the_old_name() -> None:
    leaks = []
    for name in _tracked_prose():
        for number, line in enumerate((ROOT / name).read_text().splitlines(), 1):
            if OLD_NAME.search(line) and not any(ok.search(line) for ok in ALLOWED_LINES):
                leaks.append(f"{name}:{number}: {line.strip()[:100]}")
    assert not leaks, "the old name in prose (ADR 0176 R1):\n" + "\n".join(leaks[:30])


def test_the_prose_that_is_checked_is_not_empty() -> None:
    # A path filter that silently matches nothing would make the guard above vacuous.
    names = _tracked_prose()
    assert "README.md" in names and "NOTICE" in names
    assert any(n.startswith("docs/site/") for n in names)
    assert any(n.startswith("packages/core/src/choregos_core/procedures/") for n in names)
    assert len(names) > 100


def test_the_readme_and_the_notice_name_the_former_name_once_and_say_the_new_one() -> None:
    readme = (ROOT / "README.md").read_text()
    assert readme.startswith("# Tranibor\n")
    assert "Formerly Choregos" in readme
    assert (ROOT / "NOTICE").read_text().startswith("Tranibor (formerly Choregos)\n")


def test_the_cli_installs_tranibor_and_keeps_choregos_as_the_same_entry_point() -> None:
    scripts = tomllib.loads((ROOT / "packages/cli/pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["tranibor"] == "choregos_cli.main:main"
    assert scripts["choregos"] == scripts["tranibor"], "the alias is the same main, with nothing extra"


def test_the_cli_says_tranibor() -> None:
    from choregos_cli import __version__
    from choregos_cli.main import app

    runner = CliRunner()
    assert runner.invoke(app, ["--version"]).stdout.strip() == f"tranibor {__version__}"
    help_text = runner.invoke(app, ["--help"]).stdout
    assert "Tranibor" in help_text and "Choregos" not in help_text


def test_the_default_product_name_is_tranibor() -> None:
    from choregos_api.theme import DEFAULT_THEME

    assert DEFAULT_THEME["product_name"] == "Tranibor"


def test_the_console_falls_back_to_tranibor_and_the_tab_title_ends_with_it() -> None:
    title = (ROOT / "apps/web/src/app/document-title.tsx").read_text()
    footer = (ROOT / "apps/web/src/components/edition.tsx").read_text()
    assert 'productName = "Tranibor"' in title and '? "tranibor" : productName' in title
    assert 'productName === "Tranibor"' in footer
    assert "Choregos" not in title and "Choregos" not in footer.replace("X-Choregos", "")
