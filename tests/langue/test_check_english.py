"""`tools/check_english.py`: French in a new or modified line fails `make lint` and CI (ADR 0046).

The detector is tested on both sides: French it must catch, and English it must let through —
proper names with accents, loanwords, acronyms, identifiers, code. Then the whole tool is played
against a throwaway git repository, the way CI plays it against a pull request.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


ce = _load()

# ───────────────────────────────────────── the detector ─────────────────────────────────────────

FRENCH = [
    "le train se relaie avant d'être trop long à rejouer",  # lang: fr-ok
    "Intervention humaine",  # lang: fr-ok
    "En cours",  # lang: fr-ok
    "Remettre le badge en main propre",  # lang: fr-ok
    "un secret de webhook vide",  # lang: fr-ok
    "qu'il suppose",  # lang: fr-ok
    "puis les tests",  # lang: fr-ok
    "Le correctif, et la preuve",  # lang: fr-ok
    "les champs du board GitHub Projects",  # lang: fr-ok
    "renvoie la liste dans l'ordre",  # lang: fr-ok
    "chaque statut a sa couleur",  # lang: fr-ok
]

ENGLISH = [
    "Léa's Claude Code",
    "Hélène approves the merge; Zoé reviews it",
    "Needs a human",
    "Hand the badge to its holder in person",
    "a naïve façade over the café's résumé",
    "the de facto standard",
    "Smith et al. measured it",
    "DES and AES are ciphers; EST is a time zone",
    "a sans-serif font",
    "Add a comment to the status",
    "Müller moved to Zürich",
    "the `a_revoir` state is renamed `to_review`",
    "rename `étape` to `stage`",
    "see https://example.org/docs/fr/les-etapes.html",
    "date_arrivee collaborateur_id",
    "demande",
    "O'Brien and D'Angelo",
    "don't, it's, users' data",
    "for env in local dev staging prod; do",
]


@pytest.mark.parametrize("text", FRENCH)
def test_french_is_caught(text: str) -> None:
    assert ce.looks_french(text), text


@pytest.mark.parametrize("text", ENGLISH)
def test_english_goes_through(text: str) -> None:
    assert ce.french_reason(text) is None


def test_a_lone_accented_word_is_french_a_lone_key_is_not() -> None:
    assert ce.looks_french("Décision")  # lang: fr-ok
    assert not ce.looks_french("compte")


# ─────────────────────────────── what is prose, language by language ───────────────────────────────

#: The samples below are built from this one sentence, so that this file's own French stays on
#: lines that carry the escape — the guard reads this file too.
FR = "la demande est refusée pour le train"  # lang: fr-ok

PYTHON = f'''\
def renvoyer(demande):
    """Return the request."""
    # {FR}
    resultat = calculer_le_cout(demande)  # an existing French identifier, in English
    message = "{FR}"
    other = "the request is refused"
    patch = workflow.patched("{FR}")  # {ce.ESCAPE}
    """{FR}."""
    return f"{{resultat}} {FR}"
'''


def _flagged(path: str, content: str, added: set[int] | None = None) -> set[int]:
    lines = added if added is not None else set(range(1, content.count("\n") + 2))
    return {finding.line for finding in ce.findings_in(path, content, lines)}


def test_python_reads_comments_strings_and_docstrings_not_identifiers() -> None:
    assert _flagged("apps/api/x.py", PYTHON) == {3, 5, 8, 9}


def test_only_added_lines_are_read() -> None:
    assert _flagged("apps/api/x.py", PYTHON, added={1, 4, 6}) == set()
    assert _flagged("apps/api/x.py", PYTHON, added={5}) == {5}


def test_the_escape_keeps_a_line_that_must_stay_french() -> None:
    assert 7 not in _flagged("apps/api/x.py", PYTHON)
    escaped = f'x = "{FR}"  # {ce.ESCAPE}\n'
    assert _flagged("apps/api/x.py", escaped) == set()


TSX = f"""\
// {FR}
const labels = items.map((t) => t.label);
export function Confirm() {{
  return <button title="Confirm the batch">{FR}</button>;
}}
const s = "the batch ships";
"""


def test_typescript_reads_comments_strings_and_jsx_text() -> None:
    assert _flagged("apps/web/src/x.tsx", TSX) == {1, 4}


MARKDOWN = f"""\
# Rename

The guard reads prose.

```python
# {FR}
```

{FR}.
"""


def test_markdown_skips_code_blocks() -> None:
    assert _flagged("docs/x.md", MARKDOWN) == {9}


YAML = f"""\
states:
  a_revoir:
    label: To review  # the key is an identifier
    description: {FR}
  clos: {{}}
# {FR}
"""


def test_yaml_reads_values_and_comments_not_keys() -> None:
    assert yaml.safe_load(YAML)  # a valid document
    assert _flagged("packages/core/x.yaml", YAML) == {4, 6}


def test_makefile_reads_comments_only() -> None:
    makefile = f"lint:  ## {FR}\n\tuv run ruff check les_paquets la_demande\n"
    assert _flagged("Makefile", makefile) == {1}


@pytest.mark.parametrize(
    ("path", "scanned"),
    [
        ("apps/api/src/x.py", True),
        ("docs/adr/0046-x.md", True),
        ("docs/plan/STATUS.md", True),
        ("AGENTS.md", True),
        (".github/workflows/ci.yml", True),
        ("Makefile", True),
        ("docs/fr/guide.md", False),
        ("docs/cli.md", False),
        ("apps/web/pnpm-lock.yaml", False),
        ("packages/core/src/choregos_core/injection.py", False),
        ("apps/web/public/logo.png", False),
        ("demo/README.txt", False),
    ],
)
def test_what_is_scanned(path: str, scanned: bool) -> None:
    assert ce.is_scanned(path) is scanned


def test_added_lines_from_a_unified_zero_diff() -> None:
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        "@@ -3,0 +4,2 @@\n+a\n+b\n@@ -10 +12 @@\n-c\n+d\n"
        "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    )
    assert ce.added_lines(diff) == {"x.py": {4, 5, 12}}


# ───────────────────────────── the whole tool, against a git repository ─────────────────────────────


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.org", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    module = tmp_path / "apps" / "api" / "module.py"
    module.parent.mkdir(parents=True)
    module.write_text("# Un vieux commentaire, en français\nVALEUR = 1\n", encoding="utf-8")  # lang: fr-ok
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "chore: the base")
    _git(tmp_path, "checkout", "-q", "-b", "story")
    monkeypatch.setattr(ce, "ROOT", tmp_path)
    return tmp_path


def _change(repo: pathlib.Path, line: str, message: str) -> None:
    module = repo / "apps" / "api" / "module.py"
    module.write_text(module.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", message)


def test_a_french_comment_on_a_new_line_fails(repo: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, "# On ajoute une valeur pour le train", "feat(api): one more value")  # lang: fr-ok
    assert ce.main(["--range", "main...story"]) == 1
    assert "apps/api/module.py:3" in capsys.readouterr().out


def test_english_on_new_lines_passes_beside_old_french(repo: pathlib.Path) -> None:
    _change(repo, "# One more value for the train", "feat(api): one more value")
    assert ce.main(["--range", "main...story"]) == 0


def test_a_french_commit_message_fails(repo: pathlib.Path) -> None:
    _change(repo, "AUTRE = 2", "feat(api): une valeur de plus pour le train")  # lang: fr-ok
    assert ce.main(["--range", "main...story"]) == 1
    assert ce.main(["--range", "main...story", "--no-commits"]) == 0


def test_a_page_moved_with_a_redirect_is_not_new_french(repo: pathlib.Path) -> None:
    """A renamed file keeps a redirect under its old name: git sees a copy, its old lines are not new."""
    old = repo / "apps" / "api" / "module.py"
    (repo / "apps" / "api" / "engine.py").write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    old.write_text("# Moved to engine.py\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "refactor(api): the module is the engine")
    assert ce.main(["--range", "main...story"]) == 0
    engine = repo / "apps" / "api" / "engine.py"
    added = "# Et encore une\n"  # lang: fr-ok
    engine.write_text(engine.read_text(encoding="utf-8") + added, encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "feat(api): one more")
    assert ce.main(["--range", "main...story"]) == 1, "a French line added to the moved file is still new"


# ───────────────────────────────────────── the wiring ─────────────────────────────────────────


def test_agents_md_says_english_everywhere() -> None:
    """The instructions every agent reads say what ADR 0046 decides, not what ADR 0039 §3 did."""
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    languages = agents.split("## Languages", 1)[1].split("\n## ", 1)[0]
    for needed in ("ADR 0046", "commit messages", "docs/plan/", "identifiers", "tools/check_english.py"):
        assert needed in languages, f"AGENTS.md, Languages: {needed!r} is missing"
    french = [line for _, line in ce.prose_lines("AGENTS.md", agents) if ce.looks_french(line)]
    assert not french, f"AGENTS.md is in English: {french}"


def test_make_lint_runs_the_guard() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    lint = makefile.split("\nlint:", 1)[1].split("\n\n", 1)[0]
    assert "tools/check_english.py" in lint


def test_ci_runs_the_guard_on_every_pull_request_with_history() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["english"]
    assert "needs" not in job, "behind the path filter, a docs-only pull request would skip it"
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0
    (run,) = [step["run"] for step in job["steps"] if "run" in step]
    assert "tools/check_english.py" in run
    assert "--range" in run
    assert "pull_request.base.sha" in run
    assert "english" in workflow["jobs"]["ci-ok"]["needs"]
