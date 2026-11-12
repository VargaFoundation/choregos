# SPDX-License-Identifier: Apache-2.0
"""The core names no operator (ADR 0157).

Choregos is installed and run by operators: a company that hosts an instance for its own teams, a
service provider that runs one for its customers. What one of them needed was written into the core
as if it were the platform's: an operator's name in a chart's comment, its hosts in a chart test, its
GitHub organisation in the console's demonstration data, its Jira site in a fixture. Read by anyone
else, it says the core is that operator's.

So the test reads every tracked file for the names below, in any case, and allows three things:

  * history, which says where something was measured or decided: the ADRs, `docs/plan/`, the trial
    records of `trials/`;
  * the two legal notices, `NOTICE` and `docs/TRADEMARK.md`, which name the companies they are
    about and change only with the notices themselves;
  * this file, which spells what it looks for.

An example in code, a test or the documentation uses `example.com`, `acme` or `globex`.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: An operator's names: its company name (any case, any suffix: a host, a GitHub organisation, a
#: plugin) and the Jira site a fixture was mirrored from.
OPERATOR = re.compile(r"diametral|fondation\.atlassian\.net", re.IGNORECASE)

#: Records of what happened where: never rewritten.
HISTORY = ("docs/adr/", "docs/plan/", "trials/")
#: Legal notices naming the companies they are about.
NOTICES = frozenset({"NOTICE", "docs/TRADEMARK.md"})
#: This file, which has to spell what it looks for.
SELF = pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix()


def _tracked_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [name for name in listed.decode().split("\0") if name]


def _offenders(names: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name in names:
        path = ROOT / name
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        lines = [
            f"{number}: {line.strip()[:120]}"
            for number, line in enumerate(text.splitlines(), start=1)
            if OPERATOR.search(line)
        ]
        if lines:
            found[name] = lines
    return found


def test_there_is_a_tree_to_check() -> None:
    """Without this guard, a listing that returned nothing would make the suite green."""
    assert len(_tracked_files()) > 1000


def test_the_pattern_catches_an_operator_in_any_form() -> None:
    caught = (
        "choregos-api.internal.dev.DIAMETRAL.com",
        "DiametralGroup/choregos-sandbox-dev#4",
        "plugin:diametral-delivery/x",
        "https://fondation.atlassian.net/rest/api/3/issue/1",
    )
    spared = ("choregos-api.internal.dev.example.com", "acme/choregos-sandbox-dev#4", "a diameter")
    assert all(OPERATOR.search(text) for text in caught)
    assert not any(OPERATOR.search(text) for text in spared)


def test_the_core_names_no_operator() -> None:
    files = [
        name
        for name in _tracked_files()
        if not name.startswith(HISTORY) and name not in NOTICES and name != SELF
    ]
    offenders = _offenders(files)
    assert not offenders, (
        "The core names an operator:\n  "
        + "\n  ".join(f"{name}:{line}" for name, lines in sorted(offenders.items()) for line in lines)
        + "\nUse a neutral example (example.com, acme, globex); history and the legal notices are the "
        "only exceptions (tests/licences/test_no_operator_in_core.py)."
    )


def test_every_exception_is_still_needed() -> None:
    """A notice that stops naming an operator leaves the list: the exceptions only shrink."""
    stale = sorted(NOTICES - set(_offenders(sorted(NOTICES))))
    assert not stale, f"no longer names an operator, remove it from NOTICES: {stale}"
