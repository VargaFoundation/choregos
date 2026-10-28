"""The English ratchet (ADR 0098): French is counted per file, in three passes, and only goes down.

`tools/check_english.py --all` counts, for every tracked file, the French prose lines, the distinct
French names the file declares and whether a component of its path is French. The counts live in
`tools/english_baseline/<area>.tsv`; a file above its baseline, or a new file with French, fails.
Here every rule is played on synthetic strings, on temporary baseline directories and on a
throwaway git repository — the real tree is checked by `test_english_packages.py`.

The French samples below are built from names and sentences that carry `lang: fr-ok` on their own
line, so that the guard that reads this file stays quiet.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
from types import ModuleType

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english", ROOT / "tools" / "check_english.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


ce = _load()

#: One French sentence, reused so that this file's own French stays on lines that carry the escape.
FR = "la demande est refusée pour le train"  # lang: fr-ok
API = "apps/api/src/choregos_api"


def _in_a_git_checkout() -> bool:
    try:
        subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, check=True, capture_output=True
        )
    except (subprocess.CalledProcessError, OSError):
        return False
    return True


def _identifiers(path: str, content: str) -> list[tuple[int, str]]:
    """(line, reason) of the identifier findings of a file."""
    return [(f.line, f.reason) for f in ce.file_findings(path, content) if f.kind == "identifier"]


# ───────────────────────────────────── declarations, not uses ─────────────────────────────────────


def test_python_declarations_are_read_not_uses() -> None:
    source = "def charger(x):\n    return valider(x)\n"  # lang: fr-ok
    found = _identifiers(f"{API}/x.py", source)
    assert [line for line, _ in found] == [1]
    assert "'charger'" in found[0][1]  # lang: fr-ok
    assert not any("valider" in reason for _, reason in found)  # lang: fr-ok


def test_python_declarations_cover_every_kind_of_declaration() -> None:
    source = (
        "import json as texte\n"  # lang: fr-ok
        "class Rapport:\n"  # lang: fr-ok
        "    def run(self, jeton):\n"  # lang: fr-ok
        "        self.cle = jeton\n"  # lang: fr-ok
        "        for ligne in []:\n"  # lang: fr-ok
        "            pass\n"
        "async def appeler():\n"  # lang: fr-ok
        "    pass\n"
    )
    assert sorted(ce.python_declarations(source)) == [
        (1, "texte"),  # lang: fr-ok
        (2, "Rapport"),  # lang: fr-ok
        (3, "jeton"),  # lang: fr-ok
        (3, "run"),
        (3, "self"),
        (4, "cle"),  # lang: fr-ok
        (5, "ligne"),  # lang: fr-ok
        (7, "appeler"),  # lang: fr-ok
    ]


def test_a_python_file_that_does_not_parse_declares_nothing() -> None:
    assert list(ce.python_declarations("def (:\n")) == []


def test_typescript_declarations_are_read() -> None:
    flagged = "export function grapheDesCouts() {}\n"  # lang: fr-ok
    assert list(ce.ts_declarations(flagged)) == [(1, "grapheDesCouts")]  # lang: fr-ok
    assert _identifiers("apps/web/src/x.ts", flagged) != []
    assert _identifiers("apps/web/src/x.ts", "const total = 1\n") == []
    assert _identifiers("apps/web/src/x.ts", "// const grapheDesCouts = 1\n") == []  # lang: fr-ok
    assert _identifiers("apps/web/src/x.ts", "const s = 'function grapheDesCouts'\n") == []  # lang: fr-ok


def test_typescript_line_numbers_hold_across_comments_and_strings() -> None:
    source = "/* a\n comment */\nconst s = `two\nlines`;\ninterface Rapport {}\n"  # lang: fr-ok
    assert list(ce.ts_declarations(source)) == [(3, "s"), (5, "Rapport")]  # lang: fr-ok


# ───────────────────────────────────── the lexicon of names ─────────────────────────────────────


def test_camel_case_is_split_for_code() -> None:
    for french in ("FakeFournisseur", "RapportAcces"):  # lang: fr-ok
        assert ce.code_identifier_reason(french), french
    for english in ("ClientMcp", "McpClient", "OutboundProxyCard"):
        assert ce.code_identifier_reason(english) is None, english


@pytest.mark.parametrize(
    "name", ["A2aDeclaredCost", "a2a_transport", "A2A_MAX_FILE_BYTES_DEFAULT", "test_ont001_invalid_yaml"]
)
def test_tokens_with_digits_are_dropped(name: str) -> None:
    assert ce.code_identifier_reason(name) is None


def test_a_token_with_a_digit_hides_its_french_word() -> None:
    """The documented price of dropping digit tokens: `cle2` is not read, `cle_2` is."""  # lang: fr-ok
    assert ce.code_identifier_reason("cle2") is None  # lang: fr-ok
    assert ce.code_identifier_reason("cle_2")  # lang: fr-ok


def test_two_distinct_weak_words_are_needed() -> None:
    for english in (
        "test_le_run",
        "test_le_le_run",
        "test_a_tag_must_be_a_lowercase_slug",
        "test_a_rise_and_a_new_french_file_fail",
    ):
        assert ce.code_identifier_reason(english) is None, english
    for french in (
        "test_l_index_de_la_page",
        "_proprietaire_de_l_agent",
        "test_un_slug_pris_est_un_conflit",
    ):
        assert ce.code_identifier_reason(french), french


def test_the_english_words_the_prototype_found_are_out_of_the_lexicon() -> None:
    """`depart` is a verb (`depart_now`), `coupe` a car, `lister` is English code now."""
    for word in ("depart", "departs", "coupe", "lister"):  # lang: fr-ok
        assert word not in ce.CODE_IDENTIFIER_WORDS
    assert ce.code_identifier_reason("depart_now") is None  # lang: fr-ok
    # The lexicon of ADR 0039's identifiers stays what it was: the template test reads it as is.
    assert "coupe" in ce.IDENTIFIER_WORDS  # lang: fr-ok
    assert ce.french_identifier_words("date_arrivee") == ["arrivee"]  # lang: fr-ok
    assert ce.french_identifier_words("dateArrivee") == []  # camelCase is kept whole  # lang: fr-ok


# ───────────────────────────────────────── paths ─────────────────────────────────────────


def test_path_components_are_judged_whole() -> None:
    assert ce.path_reason(f"{API}/greffons.py")  # lang: fr-ok
    for english in (
        f"{API}/extensions.py",
        f"{API}/a2a/door.py",
        f"{API}/services/plugin_servers.py",
        "apps/web/src/components/integrations/a2a.tsx",
        "docs/adr/0047-a-budget-is-a-reservation.md",
        "tests/replay/histories/wi-panier-DEMO-1e.json",  # lang: fr-ok
    ):
        assert ce.path_reason(english) is None, english


def test_a_directory_of_a_path_counts_for_its_files() -> None:
    assert ce.path_reason("tests/paquets/test_x.py")  # lang: fr-ok
    assert [f.kind for f in ce.file_findings("essai/lakehouse/README.md", "# A title\n")] == [
        "path"
    ]  # lang: fr-ok


def test_frozen_paths_are_the_four_recorded_workflow_ids() -> None:
    assert len(ce.FROZEN_PATHS) == 4
    for path, reason in ce.FROZEN_PATHS.items():
        assert "ADR 0098" in reason
        assert (ROOT / path).is_file(), f"{path}: no longer there, remove it from FROZEN_PATHS"
        assert ce.path_reason(path) is None
    assert ce.path_reason("tests/replay/histories/wi-panier-DEMO-9z.json")  # lang: fr-ok


# ──────────────────────────────────── counting, per file ────────────────────────────────────


def test_identifiers_count_once_per_distinct_name() -> None:
    two = "def a():\n    cle = 1\n\n\ndef b():\n    cle = 2\n"  # lang: fr-ok
    assert len(_identifiers(f"{API}/x.py", two)) == 1
    assert len(_identifiers(f"{API}/x.py", two + "cle = 3\n")) == 1  # lang: fr-ok
    assert _identifiers(f"{API}/x.py", "cle = 1  # lang: fr-ok\n") == []  # lang: fr-ok


def test_a_name_counts_while_one_of_its_declarations_is_unescaped() -> None:
    source = "cle = 1  # lang: fr-ok\ncle = 2\n"  # lang: fr-ok
    assert [line for line, _ in _identifiers(f"{API}/x.py", source)] == [2]


def test_prose_counts_one_per_french_line_and_skips_escaped_lines() -> None:
    source = f'# {FR}\nx = 1  # {FR}  # lang: fr-ok\n# {FR}\n"""Two lines.\n{FR}.\n{FR}.\n"""\n'
    found = [f for f in ce.file_findings(f"{API}/x.py", source) if f.kind == "prose"]
    assert [f.line for f in found] == [1, 3, 5, 6]


def test_the_three_passes_are_told_apart() -> None:
    source = f"# {FR}\ndef charger():\n    pass\n"  # lang: fr-ok
    kinds = sorted(f.kind for f in ce.file_findings(f"{API}/greffons.py", source))  # lang: fr-ok
    assert kinds == ["identifier", "path", "prose"]


def test_fr_ok_file_header_skips_the_file() -> None:
    body = "nouveau_ticket = new_work_item\n"  # lang: fr-ok
    header = "# SPDX-License-Identifier: Apache-2.0\n# lang: fr-ok-file alias:internal — a shim\n"
    path = f"{API}/greffons.py"  # lang: fr-ok
    assert ce.file_findings(path, header + body) == []
    assert {f.kind for f in ce.file_findings(path, body)} == {"identifier", "path"}


def test_the_file_escape_is_read_in_the_first_five_lines_only() -> None:
    late = "x = 1\n" * 5 + "# lang: fr-ok-file\nnouveau_ticket = 1\n"  # lang: fr-ok
    assert ce.has_file_escape("# lang: fr-ok-file\n")
    assert ce.has_file_escape("x = 1\n" * 4 + "# lang: fr-ok-file\n")
    assert not ce.has_file_escape(late)
    assert [f.kind for f in ce.file_findings(f"{API}/x.py", late)] == ["identifier"]


# ───────────────────────────────── what is read, file type by file type ─────────────────────────────────

READERS = [
    ("infra/scripts/run.sh", f"#!/bin/sh\n# {FR}\necho ok\n"),
    ("infra/policies/x.toml", f"[tool]\n# {FR}\nkey = 1\n"),
    ("docker/api.Dockerfile", f"FROM python:3.12\n# {FR}\nRUN true\n"),
    ("docker/Dockerfile", f"FROM python:3.12\n# {FR}\nRUN true\n"),
    ("justfile", f"build:\n    # {FR}\n    true\n"),
    ("CODEOWNERS", f"# {FR}\n* @team\n"),
    ("apps/api/src/choregos_api/migrations/x.sql", f"SELECT 1; -- {FR}\n"),
    ("apps/web/public/x.js", f"// {FR}\nconst a = 1;\n"),
    ("apps/web/public/x.mjs", f"/* {FR} */\nexport const a = 1;\n"),
    ("apps/web/src/x.css", f"/* {FR} */\na {{ color: red }}\n"),
    ("apps/web/src-tauri/x.rs", f"// {FR}\nfn main() {{}}\n"),
    ("templates/x/page.html", f'<p class="a">{FR}</p>\n'),
    ("templates/x/note.md.j2", f"{{% if x %}}{FR}{{% endif %}}\n"),
]


@pytest.mark.parametrize(("path", "content"), READERS, ids=[path for path, _ in READERS])
def test_every_file_type_is_read(path: str, content: str) -> None:
    assert ce.is_scanned(path), path
    assert [f.line for f in ce.file_findings(path, content) if f.kind == "prose"] != [], path


def test_markup_is_not_read_as_prose() -> None:
    page = '<p class="la-demande">the request</p>\n{{ la_demande }} {% set les_deux = 1 %}\n'  # lang: fr-ok
    assert ce.file_findings("templates/x/page.html", page) == []


def test_new_directories_are_scanned() -> None:
    for path in (
        "essai/rh/README.md",
        "infra/policies/images.yaml",
        "docker/api.Dockerfile",
        "examples/agents/plain/x.py",
        "compose.yaml",
        "CODEOWNERS",
        "justfile",
        "pyproject.toml",
        "cliff.toml",
        "integrations/x/run.sh",
        "trials/x/README.md",
    ):
        assert ce.is_scanned(path), path


def test_json_and_excluded_paths_are_not_read() -> None:
    for path in (
        "apps/web/package.json",
        "tests/replay/histories/x.json",
        "docs/fr/guide.md",
        "packages/core/src/choregos_core/injection.py",
        "docs/cli.md",
    ):
        assert not ce.is_scanned(path), path


# ───────────────────────────────────── areas ─────────────────────────────────────


def test_there_are_23_areas_and_the_longest_prefix_wins() -> None:
    assert len(ce.AREAS) == 23
    expected = {
        f"{API}/mcp/tools.py": "api-mcp",
        f"{API}/routers/x.py": "api-routers",
        f"{API}/services/x.py": "api-services",
        f"{API}/schemas/x.py": "api-schemas",
        f"{API}/migrations/versions/x.py": "api-migrations",
        f"{API}/db/models.py": "api-subsystems",
        f"{API}/a2a/door.py": "api-subsystems",
        f"{API}/egress/x.py": "api-subsystems",
        f"{API}/knowledge/x.py": "api-subsystems",
        "apps/api/tests/test_x.py": "api-tests",
        f"{API}/main.py": "api-root",
        "apps/api/pyproject.toml": "api-root",
        "apps/orchestrator/tests/test_x.py": "orchestrator-tests",
        "apps/orchestrator/src/x.py": "orchestrator-src",
        "apps/web/src/x.ts": "web",
        "packages/core/src/x.py": "core",
        "packages/adapters/src/x.py": "adapters",
        "packages/runner/src/x.py": "runner",
        "packages/contracts/openapi.yaml": "contracts",
        "packages/cli/src/x.py": "packages-other",
        "tests/langue/test_x.py": "tests-language",
        "tests/charts/test_x.py": "tests-language",
        "tests/replay/test_x.py": "tests",
        "docs/plan/STATUS.md": "docs-plan",
        "docs/usage.md": "docs",
        "essai/lakehouse/README.md": "trials",
        "charts/choregos/values.yaml": "ops",
        "infra/policies/x.yaml": "ops",
        "docker/api.Dockerfile": "ops",
        "Makefile": "repo",
        "tools/check_english.py": "repo",
    }
    for path, area in expected.items():
        assert ce.area_of(path) == area, path


@pytest.mark.skipif(not _in_a_git_checkout(), reason="not a git checkout")
def test_every_tracked_file_has_one_area() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split("\0")
    assert tracked
    for path in filter(None, tracked):
        longest = max(len(p) for prefixes in ce.AREAS.values() for p in prefixes if path.startswith(p))
        owners = {
            name
            for name, prefixes in ce.AREAS.items()
            if any(path.startswith(p) and len(p) == longest for p in prefixes)
        }
        assert owners == {ce.area_of(path)}, f"{path}: {sorted(owners)}"


# ───────────────────────────────────── baselines ─────────────────────────────────────


def test_a_rise_and_a_new_french_file_fail() -> None:
    rises, stale = ce.compare({"a.py": 3, "b.py": 1}, {"a.py": 2})
    assert [(r.path, r.found, r.baseline) for r in rises] == [("a.py", 3, 2), ("b.py", 1, 0)]
    assert stale == []


def test_a_lower_count_or_a_gone_file_is_stale_not_a_failure(
    tree: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rises, stale = ce.compare({"a.py": 1}, {"a.py": 2, "gone.py": 1})
    assert rises == []
    assert [(s.path, s.found, s.baseline) for s in stale] == [("a.py", 1, 2), ("gone.py", 0, 1)]
    # the same, through the command line, on a temporary baseline directory
    baselines = tmp_path / "baselines"
    ce.write_baselines({f"{API}/module.py": 2, "apps/api/gone.py": 1}, ["api-root"], baselines, init=True)
    assert ce.main(["--all", "--baseline-dir", str(baselines)]) == 0
    assert (
        "stale: api-root — run tools/check_english.py --update-baseline --area api-root"
        in capsys.readouterr().out
    )
    assert ce.main(["--all", "--strict", "--baseline-dir", str(baselines)]) == 1
    assert ce.main(["--update-baseline", "--area", "api-root", "--baseline-dir", str(baselines)]) == 0
    assert ce.load_baselines(baselines)["api-root"] == {f"{API}/module.py": 1}
    assert ce.main(["--all", "--strict", "--baseline-dir", str(baselines)]) == 0


def test_an_equal_count_is_neither() -> None:
    assert ce.compare({"a.py": 2}, {"a.py": 2}) == ([], [])


def test_a_baseline_file_has_a_header_and_sorted_non_zero_counts(tmp_path: pathlib.Path) -> None:
    counts = {"apps/api/b.py": 2, "apps/api/a.py": 1, "apps/api/z.py": 0, "docs/plan/x.md": 4}
    ce.write_baselines(counts, ["api-root", "docs-plan"], tmp_path, init=True)
    assert (tmp_path / "api-root.tsv").read_text(encoding="utf-8") == (
        "# api-root: French findings per file (ADR 0098). "
        "Counts only go down: tools/check_english.py --update-baseline\n"
        "apps/api/a.py\t1\napps/api/b.py\t2\n"
    )
    assert ce.load_baselines(tmp_path) == {
        "api-root": {"apps/api/a.py": 1, "apps/api/b.py": 2},
        "docs-plan": {"docs/plan/x.md": 4},
    }


def test_update_only_lowers_and_drops_zero(tmp_path: pathlib.Path) -> None:
    ce.write_baselines(
        {"apps/api/a.py": 2, "apps/api/b.py": 1, "apps/api/c.py": 1}, ["api-root"], tmp_path, init=True
    )
    before = (tmp_path / "api-root.tsv").read_bytes()
    with pytest.raises(ValueError, match=r"apps/api/a\.py"):
        ce.write_baselines({"apps/api/a.py": 3, "apps/api/b.py": 0}, ["api-root"], tmp_path)
    assert (tmp_path / "api-root.tsv").read_bytes() == before, "a refused update writes nothing"
    with pytest.raises(ValueError, match=r"apps/api/new\.py"):
        ce.write_baselines({"apps/api/a.py": 2, "apps/api/new.py": 1}, ["api-root"], tmp_path)
    # a lowered count is written, a zero and a file that is gone are dropped
    ce.write_baselines({"apps/api/a.py": 1, "apps/api/b.py": 0}, ["api-root"], tmp_path)
    assert ce.load_baselines(tmp_path) == {"api-root": {"apps/api/a.py": 1}}


def test_a_refused_area_stops_the_other_areas_too(tmp_path: pathlib.Path) -> None:
    ce.write_baselines(
        {"apps/api/a.py": 1, "docs/plan/x.md": 1}, ["api-root", "docs-plan"], tmp_path, init=True
    )
    with pytest.raises(ValueError, match=r"docs/plan/x\.md"):
        ce.write_baselines({"apps/api/a.py": 0, "docs/plan/x.md": 2}, ["api-root", "docs-plan"], tmp_path)
    assert ce.load_baselines(tmp_path)["api-root"] == {"apps/api/a.py": 1}


def test_an_unknown_area_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="unknown area"):
        ce.write_baselines({}, ["nowhere"], tmp_path, init=True)


def test_init_refuses_when_baselines_exist(tmp_path: pathlib.Path) -> None:
    (tmp_path / "core.tsv").write_text("# core\n", encoding="utf-8")
    assert ce.main(["--update-baseline", "--init", "--baseline-dir", str(tmp_path)]) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["core.tsv"], "nothing else was written"


def test_init_alone_and_strict_alone_are_usage_errors() -> None:
    with pytest.raises(SystemExit):
        ce.main(["--init"])
    with pytest.raises(SystemExit):
        ce.main(["--strict"])


# ───────────────────────────────── the whole tool, on a throwaway tree ─────────────────────────────────


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.org", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def tree(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A git repository with one French prose line in an API module, and baselines in `baselines/`."""
    repo = tmp_path / "repo"
    module = repo / API / "module.py"
    module.parent.mkdir(parents=True)
    module.write_text(f"# {FR}\nVALUE = 1\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Rules\n\nEnglish.\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "chore: the base")
    monkeypatch.setattr(ce, "ROOT", repo)
    return repo


def _track(repo: pathlib.Path, path: str, content: str) -> None:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8")
    _git(repo, "add", path)


def test_init_then_all_passes_and_a_new_french_comment_fails(
    tree: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baselines = str(tmp_path / "baselines")
    assert ce.main(["--update-baseline", "--init", "--baseline-dir", baselines]) == 0
    assert len(list((tmp_path / "baselines").glob("*.tsv"))) == 23
    assert ce.load_baselines(tmp_path / "baselines")["api-root"] == {f"{API}/module.py": 1}
    assert ce.main(["--all", "--baseline-dir", baselines]) == 0
    _track(tree, f"{API}/module.py", f"# {FR}\n# {FR}\nVALUE = 1\n")
    capsys.readouterr()
    assert ce.main(["--all", "--baseline-dir", baselines]) == 1
    assert f"rise: api-root: {API}/module.py: 2 finding(s), baseline 1" in capsys.readouterr().out


def test_a_new_french_file_and_a_new_french_path_fail(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    baselines = str(tmp_path / "baselines")
    assert ce.main(["--update-baseline", "--init", "--baseline-dir", baselines]) == 0
    _track(tree, f"{API}/greffon.py", "VALUE = 1\n")  # lang: fr-ok
    assert ce.main(["--all", "--baseline-dir", baselines]) == 1
    _git(tree, "rm", "-q", "-f", f"{API}/greffon.py")  # lang: fr-ok
    _track(tree, f"{API}/other.py", "def charger():\n    pass\n")  # lang: fr-ok
    assert ce.main(["--all", "--baseline-dir", baselines]) == 1
    _track(tree, f"{API}/other.py", "def load():\n    pass\n")
    assert ce.main(["--all", "--baseline-dir", baselines]) == 0


def test_all_without_baselines_fails_and_says_how_to_create_them(
    tree: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ce.main(["--all", "--baseline-dir", str(tmp_path / "none")]) == 1
    assert "--update-baseline --init" in capsys.readouterr().out


def test_area_limits_what_is_checked(tree: pathlib.Path, tmp_path: pathlib.Path) -> None:
    baselines = tmp_path / "baselines"
    ce.write_baselines({}, ["api-root", "docs"], baselines, init=True)
    assert ce.main(["--all", "--area", "docs", "--baseline-dir", str(baselines)]) == 0
    assert ce.main(["--all", "--area", "api-root", "--baseline-dir", str(baselines)]) == 1


def test_report_gives_the_totals_per_pass(tree: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    _track(tree, f"{API}/greffons.py", "def charger():\n    pass\n")  # lang: fr-ok
    assert ce.main(["--report"]) == 0
    rows = {line.split()[0]: line.split()[1:] for line in capsys.readouterr().out.splitlines()}
    assert rows["api-root"] == ["1", "1", "1", "3"]
    assert rows["TOTAL"] == ["1", "1", "1", "3"]


def test_the_tree_scan_outside_a_git_checkout_says_so(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ce, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="not a git checkout"):
        ce.scan_tree()
    assert ce.main(["--all", "--baseline-dir", str(tmp_path)]) == 1


def test_the_default_diff_mode_is_unchanged(tree: pathlib.Path) -> None:
    _git(tree, "checkout", "-q", "-b", "story")
    _track(tree, f"{API}/module.py", f"# {FR}\nVALUE = 1\n# One more line\n")
    _git(tree, "commit", "-q", "-m", "feat(api): one more line")
    assert ce.main(["--range", "main...story"]) == 0, "old French on untouched lines is not read"
    _track(tree, f"{API}/module.py", f"# {FR}\nVALUE = 1\n# {FR}\n")
    _git(tree, "commit", "-q", "-m", "feat(api): a French line")
    assert ce.main(["--range", "main...story"]) == 1
