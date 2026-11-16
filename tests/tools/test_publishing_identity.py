# SPDX-License-Identifier: Apache-2.0
"""The publishing identity is defined once, and moving it is one edit and one command (ADR 0156).

`packages/core/src/choregos_core/publishing.json` says who publishes Choregos and where. The code reads
it, the console reads its copy, and the files that cannot read a JSON file are listed in
`tools/publishing_identity.py`, which checks them and rewrites them. Three properties:

* the tree follows the definition: no file spells the identity unless it is the definition, its
  copy, a listed derived file or history, and a derived file spells it exactly as the definition
  does (`make publishing-check`);
* the check refuses what would make a move a hunt again: a literal in code, a derived file that is
  code, a value spelled in another case, a stale entry, a copy that drifted;
* a new identity written into the definition, then `--rewrite-from`, leaves nothing of the former one
  outside history, and history untouched.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from dataclasses import asdict

import pytest
from choregos_core.publishing import PUBLISHING

from tools import publishing_identity as pi

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Every value moves: another owner, another registry, another forge, another documentation domain.
ELSEWHERE = {
    "owner": "ExampleOrg",
    "registry": "registry.example.org/exampleorg",
    "source_repository": "https://forge.example.org/exampleorg/choregos",
    "documentation": "https://docs.example.org/choregos",
    "security_contact": "security@example.org",
    "commit_email": "bot@example.org",
    "plugin_repository": "https://forge.example.org/exampleorg/choregos-agents",
    "plugin_index": "oci://registry.example.org/exampleorg/choregos-agents/index:latest",
}


def _repository(tmp_path: pathlib.Path, names: list[str]) -> pathlib.Path:
    """A git repository holding `names`, copied from this one."""
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_the_tree_follows_the_definition() -> None:
    assert pi.problems(ROOT) == []


def test_the_scan_sees_the_whole_tree() -> None:
    """Without this, a scan that read nothing (a moved root, an empty listing) would pass."""
    found = pi.occurrences(ROOT, pi.current_identity(ROOT))
    assert len(pi.tracked_files(ROOT)) > 1000
    assert set(pi.DERIVED) <= set(found)
    assert all((ROOT / place).is_dir() for place in pi.HISTORY), "a history directory moved"


def test_the_definition_is_what_the_code_reads() -> None:
    assert pi.current_identity(ROOT) == asdict(PUBLISHING)
    copy = json.loads((ROOT / "apps/web/src/lib/publishing.json").read_text(encoding="utf-8"))
    assert copy == json.loads((ROOT / pi.CANONICAL).read_text(encoding="utf-8"))


def test_the_derived_defaults_are_the_definition() -> None:
    """The defaults an operator inherits from the files that cannot read the definition."""
    values = (ROOT / "charts/choregos/values.yaml").read_text(encoding="utf-8")
    assert f"imageRegistry: {PUBLISHING.registry}\n" in values
    assert f"index: {PUBLISHING.plugin_index}\n" in values
    assert f"identity: {PUBLISHING.plugin_signing_identity.replace('.', chr(92) + '.')}\n" in values
    assert f"registry: {PUBLISHING.registry}\n" in (ROOT / "charts/choregos-outpost/values.yaml").read_text(
        "utf-8"
    )
    images = (ROOT / "infra/policies/images.yaml").read_text(encoding="utf-8")
    assert f'imageReferences: ["{PUBLISHING.registry}/choregos-*"]' in images
    assert f'subject: "{PUBLISHING.source_repository}/.github/workflows/*"' in images
    lock = (ROOT / "plugins/offline/plugins.lock").read_text(encoding="utf-8")
    assert f"index: {PUBLISHING.plugin_index.removeprefix('oci://')}\n" in lock
    assert lock.count(f"signature: keyless, {PUBLISHING.plugin_signing_identity}\n") == lock.count(
        "signature:"
    )


# ───────────────────────────── what the check refuses ─────────────────────────────


@pytest.fixture
def small(tmp_path: pathlib.Path) -> pathlib.Path:
    return _repository(tmp_path, [pi.CANONICAL, *pi.COPIES, "charts/choregos/values.yaml"])


def _write(root: pathlib.Path, name: str, text: str) -> None:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=root, check=True)


def test_a_small_tree_passes_first(small: pathlib.Path) -> None:
    assert pi.problems(small, derived=["charts/choregos/values.yaml"]) == []


def test_a_literal_in_code_is_refused(small: pathlib.Path) -> None:
    _write(small, "apps/api/src/choregos_api/x.py", f'IMAGE = "{PUBLISHING.registry}/choregos-api:1.0.0"\n')
    (issue,) = pi.problems(small, derived=["charts/choregos/values.yaml"])
    assert issue.startswith("apps/api/src/choregos_api/x.py: spells the publishing identity (1: ")
    assert "choregos_core.publishing" in issue


def test_code_cannot_be_listed_as_derived(small: pathlib.Path) -> None:
    _write(small, "apps/web/src/x.ts", f'export const R = "{PUBLISHING.source_repository}";\n')
    issues = pi.problems(small, derived=["charts/choregos/values.yaml", "apps/web/src/x.ts"])
    assert issues == ["apps/web/src/x.ts: code is not a derived file, it reads choregos_core.publishing"]


def test_a_value_spelled_in_another_case_is_refused(small: pathlib.Path) -> None:
    """The rewrite replaces values as the definition spells them; another case would be left behind."""
    _write(small, "docs/x.md", f"docker pull {PUBLISHING.registry.upper()}/choregos-api\n")
    issues = pi.problems(small, derived=["charts/choregos/values.yaml", "docs/x.md"])
    spelled = PUBLISHING.registry.upper()
    assert issues == [
        f"docs/x.md:1: `{spelled}` is not spelled as in {pi.CANONICAL}; the rewrite would miss it"
    ]


def test_a_stale_or_absent_entry_and_a_drifted_copy_are_refused(small: pathlib.Path) -> None:
    _write(small, "docs/x.md", "nothing here\n")
    (small / pi.COPIES[0]).write_text("{}\n", encoding="utf-8")
    issues = pi.problems(small, derived=["charts/choregos/values.yaml", "docs/x.md", "docs/absent.md"])
    assert issues == [
        f"{pi.COPIES[0]}: differs from {pi.CANONICAL}; run `make publishing-rewrite FROM=HEAD`",
        "docs/x.md: listed in DERIVED but spells nothing of the identity any more; remove it",
        "docs/absent.md: listed in DERIVED but absent",
    ]


def test_history_may_name_it(small: pathlib.Path) -> None:
    _write(small, "docs/adr/9999-x.md", f"We published at {PUBLISHING.registry}.\n")
    assert pi.problems(small, derived=["charts/choregos/values.yaml"]) == []


def test_a_name_that_only_contains_a_value_is_not_it() -> None:
    registry = PUBLISHING.registry
    assert pi.find(f"{registry}x/a x{registry}/b", {"registry": registry}) == []
    assert pi.find(f"pull {registry}/choregos-api", {"registry": registry}) == [(1, registry)]


def test_the_owner_counts_only_as_a_github_path(small: pathlib.Path) -> None:
    """A publisher's name in prose or in a product string is not the GitHub organisation."""
    owner = PUBLISHING.owner
    prose = f"{owner}X/a x{owner}/b, published by {owner}. {owner.upper()} edition"
    assert pi.find(prose, {"owner": owner}) == []
    paths = f"see {owner}/choregos, @{owner.lower()}/team, https://github.com/{owner}"
    assert pi.find(paths, {"owner": owner}) == [(1, owner), (1, owner.lower()), (1, owner)]

    _write(small, "docs/x.md", f"published by {owner}; see {owner}/choregos and https://github.com/{owner}\n")
    old = pi.current_identity(small)
    new = old | {"owner": "ExampleOrg"}
    definition = json.loads((small / pi.CANONICAL).read_text(encoding="utf-8"))
    (small / pi.CANONICAL).write_text(json.dumps(definition | new, indent=2) + "\n", encoding="utf-8")
    pi.rewrite(small, old, new, derived=["docs/x.md", "charts/choregos/values.yaml"])
    assert (small / "docs/x.md").read_text(encoding="utf-8") == (
        f"published by {owner}; see ExampleOrg/choregos and https://github.com/ExampleOrg\n"
    )
    # The prose is not rewritten, but once the owner has moved it is reported as left behind.
    assert pi.left_behind(small, old, new) == [f"docs/x.md:1: `{owner}` is the former identity"]


def test_two_values_alike_are_refused() -> None:
    with pytest.raises(ValueError, match="hold the same value"):
        pi.identity_of(json.dumps({"owner": "a", "registry": "a"}))


# ───────────────────────────── moving it ─────────────────────────────


def test_moving_the_publication_is_one_edit_and_one_command(tmp_path: pathlib.Path) -> None:
    found = pi.occurrences(ROOT, pi.current_identity(ROOT))
    history = sorted(name for name in found if pi.is_history(name))
    root = _repository(tmp_path, sorted({pi.CANONICAL, *pi.COPIES, *pi.DERIVED, *history}))
    before = {name: (root / name).read_bytes() for name in [*pi.DERIVED, *history]}
    old = pi.current_identity(root)
    assert set(ELSEWHERE) == set(old), "a key was added: give it a new value above"

    definition = json.loads((root / pi.CANONICAL).read_text(encoding="utf-8"))
    (root / pi.CANONICAL).write_text(json.dumps(definition | ELSEWHERE, indent=2) + "\n", encoding="utf-8")
    changed = pi.rewrite(root, old, ELSEWHERE)

    assert pi.left_behind(root, old, ELSEWHERE) == []
    assert pi.problems(root, identity=ELSEWHERE) == []
    assert sorted(changed) == sorted({*pi.COPIES, *pi.DERIVED})
    assert all((root / name).read_bytes() == before[name] for name in history), "history was rewritten"
    values = (root / "charts/choregos/values.yaml").read_text(encoding="utf-8")
    assert "imageRegistry: registry.example.org/exampleorg\n" in values
    assert (
        r"identity: https://forge\.example\.org/exampleorg/choregos-agents/\.github/workflows/publish\.yml"
        in values
    )
    policy = (root / "infra/policies/plugin-servers.yaml").read_text(encoding="utf-8")
    assert r"^registry\\.example\\.org/exampleorg/choregos-agents/" in policy
    assert "@ExampleOrg/integrators" in (root / "CODEOWNERS").read_text(encoding="utf-8")


def test_a_rewrite_that_moves_one_value_leaves_the_others(tmp_path: pathlib.Path) -> None:
    """The owner moves, the registry stays: the owner inside the registry's path is the registry's."""
    root = _repository(tmp_path, [pi.CANONICAL, *pi.COPIES, "CODEOWNERS", "charts/choregos/values.yaml"])
    old = pi.current_identity(root)
    new = old | {"owner": "ExampleOrg"}
    definition = json.loads((root / pi.CANONICAL).read_text(encoding="utf-8"))
    (root / pi.CANONICAL).write_text(json.dumps(definition | new, indent=2) + "\n", encoding="utf-8")
    pi.rewrite(root, old, new, derived=["CODEOWNERS", "charts/choregos/values.yaml"])
    assert pi.left_behind(root, old, new) == []
    values = (root / "charts/choregos/values.yaml").read_text(encoding="utf-8")
    assert f"imageRegistry: {PUBLISHING.registry}\n" in values
    assert "@ExampleOrg/integrators" in (root / "CODEOWNERS").read_text(encoding="utf-8")
