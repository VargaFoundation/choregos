"""The contracts and the five frozen surfaces speak no trade, and never more of it than today (ADR 0181, C0).

ADR 0168 freezes five surfaces on 1.0: the REST contract, the MCP door, the A2A door, the trigger route
and the enterprise seams. A name still there on that day stays for the whole major version. ADR 0181
(option D, 2026-11-19) takes the software-delivery vocabulary out of them in rounds: C1 gives the core's
own concepts neutral names, C2 and D move what belongs to software delivery into the `software-delivery`
plugin. This guard is round C0's: it says where the vocabulary is today, and turns red when it grows.

What is read: `packages/contracts` (the schemas, `openapi.yaml`, `outpost-door.yaml`, the Python types of
`src/`), the MCP door (`tranibor_api/mcp/`), the A2A door (`tranibor_api/a2a/`), the trigger route
(`routers/triggers.py`, `services/triggers.py`) and the enterprise seams (`edition.py`, `extensions.py`,
`tenant_sign_in.py`). `packages/contracts/ts` is generated from the schemas and `openapi.yaml`, and a test
refuses any divergence, so guarding its sources guards it.

What is a hit: a **name**, never prose. A frozen surface freezes names (a field, an enum value, an
`operationId`, a path, a Python identifier) and not the sentences describing them, which a later release
may rewrite freely. So the guard reads the Python identifiers and the string literals that are names (no
space), and in a schema or an OpenAPI document every key and every string value that is a name, except
under `description`, `summary`, `title`, `$comment` and `examples`. A name is split into words (snake,
camel, kebab, dot and slash), and a term is a run of words: `pr_url` holds `pr`, `getRunDiff` holds
`run_diff`, `scm.pr.opened` holds `scm` and `pr`. The longest term at a position wins, so `fix_ci` is
counted once, as `fix_ci`.

The terms are the software-delivery names of ADR 0181's inventory, each with the round that takes it out.
Vendor names (`github`, `gitlab`, `tekton`) are left out: the ADR classes them (a), systems a connector or a
linked account names, not a trade. So are `diff` (an output type, a diff of any text), `review` and
`release` (an agent's release), which the inventory keeps.

The allowlist is a ratchet, checked both ways and counted: a file may hold a term exactly as many times as
`ALLOWED` says. A count that grows is refused: name the concept neutrally, or, when it is the plugin's,
put it in the plugin (ADR 0181). A count that drops must be lowered here in the same PR, and an entry that
reaches zero leaves, so the allowlist can only shrink (ADR 0099 for the ceilings). It holds nothing on a
frozen surface on 1.0.

To see it fail, add a field `pr_number` to any model of `packages/contracts/src/tranibor_contracts/`, or
`list_trains = None` to `tranibor_api/mcp/tools.py`: the test names the file, the term and its round.
"""

from __future__ import annotations

import io
import json
import re
import tokenize
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
API = "apps/api/src/tranibor_api"

#: The files the guard reads, by glob from the repository's root.
SURFACES: tuple[str, ...] = (
    "packages/contracts/schemas/*.json",
    "packages/contracts/openapi.yaml",
    "packages/contracts/outpost-door.yaml",
    "packages/contracts/src/**/*.py",
    f"{API}/mcp/**/*.py",
    f"{API}/a2a/**/*.py",
    f"{API}/routers/triggers.py",
    f"{API}/services/triggers.py",
    f"{API}/edition.py",
    f"{API}/extensions.py",
    f"{API}/tenant_sign_in.py",
)

_C1 = "C1: a neutral name for a concept the core owns"
_C2 = "C2: moves to the software-delivery plugin under the same name"
#: term -> the round of ADR 0181 that takes it out of the core's contracts.
VOCABULARY: dict[str, str] = {
    "scm": _C2 + " (the `scm.*` event types, the `scm` connector kind)",
    "ci": _C2 + " (the `ci.*` event types, the `ci` connector kind, `ci_green`)",
    "cd": _C2 + " (the `cd.*` event types, the `cd` connector kind)",
    "pr": "C1 for `pr_url` (`links`); " + _C2 + " for `open_pr`, `merge_pr` and `scm.pr.*`",
    "prs": _C2,
    "pull_request": _C1 + " (`in_change_request`), or " + _C2 + " (`pull_request.draft` of `does`)",
    "pull_requests": _C2,
    "repo": "C2: the `delivery` sections of the project and stage contracts",
    "repos": "C2: the `delivery` sections",
    "repository": _C1 + " (`workspace`)",
    "repositories": "C2: the `delivery` section of the project",
    "branch": _C1 + " (`writes_workspace`), or C2 (the `delivery` sections)",
    "branches": "C2: the `delivery` sections",
    "commits": "C2: the `delivery` block of the stage result",
    "dora": _C2,
    "gitops": "C2: the `delivery` section of the project",
    "lint": "C2: a measured fact of the plugin (`evidence.facts`)",
    "typecheck": "C2: a measured fact of the plugin (`evidence.facts`)",
    "coverage": "C2: a measured fact of the plugin (`evidence.facts`)",
    "tests_run": "C2: a measured fact of the plugin (`evidence.facts`)",
    "tests_passed": "C2: a measured fact of the plugin (`evidence.facts`)",
    "tests_failed": "C2: a measured fact of the plugin (`evidence.facts`)",
    "security_scan": "C2: a measured fact of the plugin (`evidence.facts`)",
    "diff_files": "C2: a measured fact of the plugin, and its policy section",
    "diff_lines": "C2: a measured fact of the plugin, and its policy section",
    "max_diff": "C2: the plugin's policy section",
    "run_diff": _C2,
    "merge_rate": _C1 + " (`first_pass_acceptance_rate`)",
    "developer": _C1 + " (`contributor`)",
    "release_captain": _C1 + " (`approver`)",
    "release_train": _C2,
    "train": _C2,
    "trains": _C2,
    "release_notes": _C2 + " (a stage role and an output)",
    "fix_ci": _C2 + " (a stage role); repair by role becomes `repairs:` in C1",
    "address_review": _C2 + " (a stage role); repair by role becomes `repairs:` in C1",
    "verify_prod": _C2 + " (a stage role)",
    "flaky": _C2 + " (a finding type and a memory kind)",
    "hotspot": _C2 + " (a memory kind)",
    "tech_debt": _C2 + " (a finding type)",
}

#: file -> {term: count}, measured on 1048737e8 (2026-11-19). Lower a count in the PR that removes a
#: hit, and drop the entry at zero; never raise one (ADR 0181 C0, ADR 0099).
ALLOWED: dict[str, dict[str, int]] = {
    "packages/contracts/openapi.yaml": {
        "branch": 3,
        "ci": 2,
        "coverage": 1,
        "developer": 2,
        "dora": 9,
        "max_diff": 2,
        "merge_rate": 2,
        "pr": 2,
        "pull_request": 1,
        "release_captain": 2,
        "release_train": 2,
        "repo": 16,
        "repositories": 2,
        "repository": 11,
        "run_diff": 1,
        "train": 6,
        "trains": 14,
    },
    "packages/contracts/schemas/policy.schema.json": {"max_diff": 2, "release_train": 1, "train": 4},
    "packages/contracts/schemas/project.schema.json": {
        "branch": 5,
        "gitops": 1,
        "lint": 2,
        "repo": 3,
        "repositories": 3,
        "typecheck": 2,
    },
    "packages/contracts/schemas/stage-input.schema.json": {
        "branch": 4,
        "lint": 1,
        "pr": 1,
        "repo": 1,
        "typecheck": 1,
    },
    "packages/contracts/schemas/stage-result.schema.json": {
        "branch": 1,
        "commits": 1,
        "coverage": 1,
        "diff_files": 1,
        "diff_lines": 1,
        "lint": 1,
        "pr": 1,
        "release_notes": 1,
        "security_scan": 1,
        "tests_failed": 1,
        "tests_passed": 1,
        "tests_run": 1,
        "typecheck": 1,
    },
    "packages/contracts/schemas/template.schema.json": {"repo": 1},
    "packages/contracts/schemas/workflow.schema.json": {
        "developer": 1,
        "pull_request": 1,
        "release_captain": 1,
        "release_train": 1,
        "train": 2,
    },
    "packages/contracts/src/tranibor_contracts/__init__.py": {
        "pull_request": 2,
        "repo": 4,
        "repository": 2,
        "train": 2,
    },
    "packages/contracts/src/tranibor_contracts/enums.py": {
        "address_review": 2,
        "cd": 8,
        "ci": 8,
        "developer": 2,
        "fix_ci": 2,
        "flaky": 4,
        "hotspot": 2,
        "pr": 10,
        "release_captain": 2,
        "release_notes": 2,
        "scm": 8,
        "tech_debt": 2,
        "verify_prod": 2,
    },
    "packages/contracts/src/tranibor_contracts/policy.py": {"max_diff": 2, "release_train": 1, "train": 7},
    "packages/contracts/src/tranibor_contracts/project.py": {
        "branch": 4,
        "gitops": 4,
        "lint": 1,
        "repo": 25,
        "repositories": 6,
        "repository": 6,
        "typecheck": 1,
    },
    "packages/contracts/src/tranibor_contracts/stage.py": {
        "branch": 3,
        "commits": 1,
        "coverage": 1,
        "diff_files": 1,
        "diff_lines": 1,
        "lint": 2,
        "pr": 2,
        "release_notes": 1,
        "repo": 3,
        "security_scan": 1,
        "tests_failed": 1,
        "tests_passed": 2,
        "tests_run": 2,
        "typecheck": 2,
    },
    "packages/contracts/src/tranibor_contracts/workflow.py": {
        "developer": 1,
        "pr": 9,
        "pull_request": 3,
        "release_captain": 1,
        "release_train": 2,
        "train": 5,
    },
}

_TERMS: frozenset[tuple[str, ...]] = frozenset(tuple(term.split("_")) for term in VOCABULARY)
_LONGEST = max(len(term) for term in _TERMS)
#: Keys whose value is prose: a description may say "pull request"; a name may not.
PROSE_KEYS = frozenset({"description", "summary", "title", "$comment", "examples", "example"})
#: A string that is a name, not a sentence: no space.
_NAME = re.compile(r"^[A-Za-z0-9_.\-/{}:*$#]+$")
_STRING = re.compile(r"^[rbuRBU]*('''|\"\"\"|'|\")(.*)\1$", re.S)


def words(name: str) -> list[str]:
    """`getRunDiff` -> get, run, diff; `scm.pr.opened` -> scm, pr, opened; `pr_url` -> pr, url."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name)
    return [w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w]


def terms_in(name: str) -> list[str]:
    """The vocabulary's terms in one name, the longest at each position."""
    found: list[str] = []
    split = words(name)
    i = 0
    while i < len(split):
        for size in range(_LONGEST, 0, -1):
            run = tuple(split[i : i + size])
            if len(run) == size and run in _TERMS:
                found.append("_".join(run))
                i += size
                break
        else:
            i += 1
    return found


def python_hits(source: str) -> Counter[str]:
    """The terms of a module's identifiers and of its string literals that are names."""
    counted: Counter[str] = Counter()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME:
            counted.update(terms_in(token.string))
        elif token.type == tokenize.STRING:
            literal = _STRING.match(token.string)
            if literal and _NAME.match(literal.group(2)):
                counted.update(terms_in(literal.group(2)))
    return counted


def document_hits(node: Any) -> Counter[str]:
    """The terms of a JSON or YAML document's keys and of its string values that are names."""
    counted: Counter[str] = Counter()
    if isinstance(node, dict):
        for key, value in node.items():
            counted.update(terms_in(str(key)))
            if key not in PROSE_KEYS:
                counted.update(document_hits(value))
    elif isinstance(node, list):
        for value in node:
            counted.update(document_hits(value))
    elif isinstance(node, str) and _NAME.match(node):
        counted.update(terms_in(node))
    return counted


def hits_of(path: Path) -> Counter[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return python_hits(text)
    return document_hits(json.loads(text) if path.suffix == ".json" else yaml.safe_load(text))


def surfaces() -> list[Path]:
    return sorted({path for pattern in SURFACES for path in ROOT.glob(pattern) if path.is_file()})


def measure() -> dict[str, dict[str, int]]:
    measured: dict[str, dict[str, int]] = {}
    for path in surfaces():
        counted = hits_of(path)
        if counted:
            measured[path.relative_to(ROOT).as_posix()] = dict(sorted(counted.items()))
    return measured


def test_every_surface_is_read() -> None:
    files = {path.relative_to(ROOT).as_posix() for path in surfaces()}
    for pattern in SURFACES:
        assert any(ROOT.glob(pattern)), f"`{pattern}` matches no file: the guard would read nothing there"
    assert "packages/contracts/openapi.yaml" in files
    assert f"{API}/mcp/tools.py" in files
    assert f"{API}/a2a/door.py" in files
    assert len(files) > 40, "the contracts' sources and the surfaces were found"


def test_the_contracts_and_the_surfaces_name_no_more_trade_than_allowed() -> None:
    measured = measure()
    grown: list[str] = []
    dropped: list[str] = []
    for file in sorted(set(measured) | set(ALLOWED)):
        now, allowed = measured.get(file, {}), ALLOWED.get(file, {})
        for term in sorted(set(now) | set(allowed)):
            have, may = now.get(term, 0), allowed.get(term, 0)
            if have > may:
                grown.append(f"{file}: `{term}` {have} time(s), allowed {may} ({VOCABULARY[term]})")
            elif have < may:
                dropped.append(f"{file}: `{term}` {have} time(s), the allowlist still says {may}")
    assert not grown, (
        "a software-delivery name was added to the contracts or a frozen surface (ADR 0181): name the "
        "concept neutrally, or put it in the software-delivery plugin:\n" + "\n".join(grown)
    )
    assert not dropped, (
        "the vocabulary shrank: lower ALLOWED in the same PR (an entry at zero leaves), so it never comes "
        "back:\n" + "\n".join(dropped)
    )


def test_every_allowed_term_is_a_term_of_the_vocabulary() -> None:
    unknown = {term for counts in ALLOWED.values() for term in counts} - set(VOCABULARY)
    assert not unknown, f"allowlist terms the guard does not look for: {sorted(unknown)}"
    assert all(count > 0 for counts in ALLOWED.values() for count in counts.values()), "a zero entry leaves"


def test_the_guard_reads_names_and_not_prose() -> None:
    source = (
        "class WorkItem:\n"
        '    """The pull request of the release train, in prose."""\n'
        "    pr_url: str  # a comment on the CI is prose\n"
        '    kind = "scm.pr.opened"\n'
        '    note = "the ci is green"\n'
        "    role = Role.RELEASE_CAPTAIN\n"
        "    process = isinstance(prefix, str)\n"
    )
    assert sorted(python_hits(source).elements()) == ["pr", "pr", "release_captain", "scm"]


def test_the_guard_reads_a_schema_key_and_value_and_skips_its_description() -> None:
    document = {
        "properties": {
            "pr_url": {"type": "string", "description": "the pull request on the repository"},
            "role": {"enum": ["fix_ci", "triage", "release notes"]},
        },
        "paths": {"/projects/{id}/trains": {"get": {"operationId": "getRunDiff", "summary": "CI train"}}},
    }
    assert sorted(document_hits(document).elements()) == ["fix_ci", "pr", "run_diff", "trains"]


def test_the_longest_term_wins_and_a_word_inside_a_word_is_no_hit() -> None:
    assert terms_in("fix_ci") == ["fix_ci"]
    assert terms_in("releaseTrain") == ["release_train"]
    assert terms_in("first_pass_merge_rate") == ["merge_rate"]
    assert terms_in("principal") == []
    assert terms_in("process_scope") == []
