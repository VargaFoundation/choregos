#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The files that spell the publishing identity, checked and rewritten from its one definition (ADR 0156).

    uv run python tools/publishing_identity.py                      # check: make publishing-check
    uv run python tools/publishing_identity.py --rewrite-from REF   # make publishing-rewrite FROM=REF

The identity is `packages/core/src/choregos_core/publishing.json`. The code reads it
(`choregos_core.publishing`); the console reads its copy, `apps/web/src/lib/publishing.json`. What
cannot read a JSON file — a chart's values, a Kyverno policy, a workflow, a Dockerfile, a page of
documentation — spells the values out, and is listed in `DERIVED` below.

The check refuses:

* a tracked file that spells a value of the identity and is neither the definition, its copy, a
  derived file nor history: code reads the definition, anything else is added to `DERIVED`;
* a derived file that is code (code reads the definition), that no longer spells the identity, or
  that spells it in another case or escaping than the definition (the rewrite would miss it);
* a copy that differs from the definition.

`--rewrite-from REF` reads the identity as it was at `REF` and replaces, in every derived file and
copy, each of its values (plain, and with its dots escaped once or twice as in a regular expression)
by the current one, in one pass, longest first, then checks. Moving the publication elsewhere is then
one edit of the definition and this one command. History (`HISTORY`) is never rewritten: it says what
was true when it was written.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping

ROOT = pathlib.Path(__file__).resolve().parents[1]
GIT = shutil.which("git") or "git"
CANONICAL = "packages/core/src/choregos_core/publishing.json"
COPIES = ("apps/web/src/lib/publishing.json",)

#: What records the past: decisions, the plan and its status, applied migrations, trial results.
HISTORY = (
    "docs/adr/",
    "docs/plan/",
    "apps/api/src/choregos_api/migrations/versions/",
    "trials/",
)

#: Code reads the definition; it is never a derived file, unless a generator writes it from one.
CODE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".sh", ".ps1"})
#: Code generated from a derived file (`make contracts`, from `openapi.yaml`): rewritten with it.
GENERATED_CODE = frozenset({"packages/contracts/ts/api.ts"})

#: The files that cannot read the definition and spell it out, rewritten by `--rewrite-from`.
DERIVED: tuple[str, ...] = (
    # Repository metadata: GitHub teams, notices, the Claude Code marketplace and plugin.
    ".claude-plugin/marketplace.json",
    "CODEOWNERS",
    "NOTICE",
    "README.md",
    "SECURITY.md",
    "integrations/claude-code/.claude-plugin/plugin.json",
    "packages/agent-sdk/README.md",
    "packages/agent-sdk/pyproject.toml",
    # Where the images are pushed and signed, and the upstream-only jobs.
    ".github/dependabot.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/code-agent-examples.yml",
    ".github/workflows/nightly.yml",
    ".github/workflows/release.yml",
    # The charts' defaults: registry, memory image, plugin source, runbook links, chart sources.
    "charts/choregos-outpost/Chart.yaml",
    "charts/choregos-outpost/README.md",
    "charts/choregos-outpost/values.yaml",
    "charts/choregos/Chart.yaml",
    "charts/choregos/charts/ecphoria/Chart.yaml",
    "charts/choregos/charts/ecphoria/values.yaml",
    "charts/choregos/templates/_helpers.tpl",
    "charts/choregos/tests/tenant_test.yaml",
    "charts/choregos/values.yaml",
    # The reference infrastructure: Argo CD sources, admission policies and their tests.
    "infra/README.md",
    "infra/bootstrap/agent-sandbox.yaml",
    "infra/bootstrap/app-of-apps.yaml",
    "infra/bootstrap/ecphoria.yaml",
    "infra/bootstrap/gvisor.yaml",
    "infra/bootstrap/policies.yaml",
    "infra/platform/base/application.yaml",
    "infra/platform/overlays/dev/application.yaml",
    "infra/platform/overlays/prod/application.yaml",
    "infra/platform/overlays/staging/application.yaml",
    "infra/policies/images.yaml",
    "infra/policies/plugin-servers.yaml",
    "infra/policies/tests/plugin-servers/resources.yaml",
    "infra/projects/applicationset.yaml",
    # Images built FROM the platform's, and the local install.
    ".env.example",
    "compose.yaml",
    "dev/Tiltfile",
    "dev/compose.yaml",
    "docker/api.Dockerfile",
    "docker/base.Dockerfile",
    "examples/agents/langgraph/Dockerfile",
    "examples/agents/openai-agents/Dockerfile",
    "examples/agents/plain/Dockerfile",
    "examples/agents/pydantic-ai/Dockerfile",
    "templates/github-tekton-argo-k8s/k8s/smoke-job.yaml",
    "templates/github-tekton-argo-k8s/tekton/agent-stage.yaml",
    "templates/github-tekton-argo-k8s/tekton/pipeline-agent.yaml",
    "templates/github-tekton-argo-k8s/tekton/pipeline-ci.yaml",
    # The plugins: the offline lock, the contract's examples, the test fixtures mirrored from them.
    "plugins/offline/plugins.lock",
    "packages/contracts/schemas/examples/plugin-index.example.json",
    "packages/contracts/schemas/examples/plugin-server.example.json",
    "packages/contracts/schemas/examples/plugin.example.json",
    "tests/fixtures/plugin_fakes/servicenow.json",
    "tests/fixtures/plugins/desk/plugin.yaml",
    "tests/fixtures/plugins/desk/servers/desk.yaml",
    "tests/fixtures/plugins/hello/plugin.yaml",
    "tests/fixtures/plugins/hello/servers/hello.yaml",
    "tests/fixtures/plugins/servicenow/plugin.yaml",
    "tests/fixtures/plugins/servicenow/servers/servicenow.yaml",
    # The contract's description of the documentation address, and the code generated from it.
    "packages/contracts/openapi.yaml",
    "packages/contracts/ts/api.ts",
    # A setup procedure (data the console shows) and what `make procedures` generates from it.
    "packages/core/src/choregos_core/procedures/connector.tracker.plugin.md",
    "docs/procedures/connector.tracker.plugin.md",
    "apps/web/src/mocks/procedures.json",
    # The documentation: commands, addresses and links a reader copies.
    "docs/CONTRIBUTING.md",
    "docs/cli.md",
    "docs/deployment.md",
    "docs/development.md",
    "docs/getting-started.md",
    "docs/integrations.md",
    "docs/outpost-operator.md",
    "docs/plugins.md",
    "docs/site/README.md",
    "docs/site/build/agents.md",
    "docs/site/cli/install.md",
    "docs/site/get-started.md",
    "docs/site/host/index.md",
    "docs/site/host/install.md",
    "docs/site/host/upgrades.md",
    "docs/site/index.md",
    "docs/site/integrate/mcp.md",
    "docs/usage.md",
)

#: A character that continues a name: an occurrence is not matched inside a longer word.
_NAME_CHAR = "A-Za-z0-9_-"
_NAME = re.compile(f"[{_NAME_CHAR}]")


def identity_of(text: str) -> dict[str, str]:
    """The values of a `publishing.json`: every string but the comment, each one distinct."""
    data = json.loads(text)
    values = {key: value for key, value in data.items() if key != "$comment" and isinstance(value, str)}
    seen: dict[str, str] = {}
    for key, value in values.items():
        if value in seen:
            raise ValueError(
                f"`{key}` and `{seen[value]}` hold the same value: a rewrite could not tell them apart"
            )
        seen[value] = key
    return values


def current_identity(root: pathlib.Path = ROOT) -> dict[str, str]:
    return identity_of((root / CANONICAL).read_text(encoding="utf-8"))


def _escaped(value: str) -> tuple[str, str, str]:
    """A value plain, with its dots escaped once (`\\.`) and twice (`\\\\.`), as a regex in YAML."""
    return value, value.replace(".", "\\."), value.replace(".", "\\\\.")


def spellings(value: str) -> list[str]:
    """The distinct ways a file may spell a value."""
    return list(dict.fromkeys(_escaped(value)))


def is_owner(key: str) -> bool:
    """The owner is a name, not an address: it counts only where it is a GitHub path segment."""
    return key.endswith("owner")


def _owner_place(text: str, start: int, end: int) -> bool:
    """`<owner>/…` (a repository, a team, an image path) or `github.com/<owner>` (the organisation)."""
    return text[end : end + 1] == "/" or text[max(0, start - 11) : start].lower() == "github.com/"


def _pattern(texts: Iterable[str], *, ignore_case: bool, owners: Iterable[str] = ()) -> re.Pattern[str]:
    owners = set(owners)
    parts = []
    for text in sorted(set(texts), key=lambda t: (-len(t), t)):
        before = f"(?<![{_NAME_CHAR}])" if _NAME.match(text[0]) else ""
        after = f"(?![{_NAME_CHAR}])" if _NAME.match(text[-1]) else ""
        if text in owners:
            name = re.escape(text)
            parts.append(rf"(?:(?<=github\.com/){name}{after}|{before}{name}(?=/))")
        else:
            parts.append(f"{before}{re.escape(text)}{after}")
    return re.compile("|".join(parts), re.IGNORECASE if ignore_case else 0)


def _bounded(text: str, start: int, end: int, needle: str) -> bool:
    """Whether `text[start:end]` stands alone: not inside a longer name on either side it ends with one."""
    if _NAME.match(needle[0]) and start > 0 and _NAME.match(text[start - 1]):
        return False
    return not (_NAME.match(needle[-1]) and end < len(text) and _NAME.match(text[end]))


def find(text: str, identity: Mapping[str, str]) -> list[tuple[int, str]]:
    """(line, text) of every spelling of every value in `text`, in any case, longest first, no overlap."""
    lowered = text.lower()
    needles = {s.lower(): is_owner(key) for key, value in identity.items() for s in spellings(value)}
    candidates: list[tuple[int, int]] = []
    for needle, owner in needles.items():
        start = lowered.find(needle)
        while start != -1:
            end = start + len(needle)
            if _bounded(lowered, start, end, needle) and (not owner or _owner_place(lowered, start, end)):
                candidates.append((start, end))
            start = lowered.find(needle, start + 1)
    hits: list[tuple[int, str]] = []
    covered = -1
    for start, end in sorted(candidates, key=lambda c: (c[0], -c[1])):
        if start >= covered:
            hits.append((text.count("\n", 0, start) + 1, text[start:end]))
            covered = end
    return hits


def _git(root: pathlib.Path, *args: str) -> bytes:
    return subprocess.run(  # noqa: S603 - a fixed program; the arguments are a ref and paths
        [GIT, *args], cwd=root, check=True, capture_output=True
    ).stdout


def tracked_files(root: pathlib.Path = ROOT) -> list[str]:
    return [name for name in _git(root, "ls-files", "-z").decode().split("\0") if name]


def _text(path: pathlib.Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


def is_history(name: str) -> bool:
    return name.startswith(HISTORY)


def occurrences(root: pathlib.Path, identity: Mapping[str, str]) -> dict[str, list[tuple[int, str]]]:
    """Tracked file -> (line, text) of each spelling of the identity it holds, in any case."""
    found: dict[str, list[tuple[int, str]]] = {}
    for name in tracked_files(root):
        text = _text(root / name)
        hits = find(text, identity) if text is not None else []
        if hits:
            found[name] = hits
    return found


def problems(
    root: pathlib.Path = ROOT,
    derived: Iterable[str] = DERIVED,
    identity: Mapping[str, str] | None = None,
) -> list[str]:
    """Why the tree does not follow the definition; empty when it does."""
    identity = dict(identity) if identity is not None else current_identity(root)
    derived = tuple(derived)
    exact = {s for value in identity.values() for s in spellings(value)}
    found = occurrences(root, identity)
    canonical = (root / CANONICAL).read_text(encoding="utf-8")
    issues: list[str] = []
    for copy in COPIES:
        if _text(root / copy) != canonical:
            issues.append(f"{copy}: differs from {CANONICAL}; run `make publishing-rewrite FROM=HEAD`")
    for name in derived:
        if pathlib.PurePosixPath(name).suffix in CODE_SUFFIXES and name not in GENERATED_CODE:
            issues.append(f"{name}: code is not a derived file, it reads choregos_core.publishing")
        elif not (root / name).is_file():
            issues.append(f"{name}: listed in DERIVED but absent")
        elif name not in found:
            issues.append(f"{name}: listed in DERIVED but spells nothing of the identity any more; remove it")
    allowed = {CANONICAL, *COPIES, *derived}
    for name, hits in sorted(found.items()):
        if is_history(name):
            continue
        if name not in allowed:
            lines = ", ".join(f"{number}: {text}" for number, text in hits[:3])
            issues.append(
                f"{name}: spells the publishing identity ({lines}); code reads choregos_core.publishing "
                "(or the console's copy), a file that cannot read it is added to DERIVED"
            )
            continue
        for number, text in hits:
            if text not in exact:
                issues.append(
                    f"{name}:{number}: `{text}` is not spelled as in {CANONICAL}; the rewrite would miss it"
                )
    return issues


def rewrite(
    root: pathlib.Path,
    old: Mapping[str, str],
    new: Mapping[str, str],
    derived: Iterable[str] = DERIVED,
) -> list[str]:
    """Replaces the `old` identity by the `new` one in every derived file and copy; returns what changed."""
    if set(old) != set(new):
        raise ValueError(f"the identity's keys changed ({sorted(set(old) ^ set(new))}): rewrite by hand")
    mapping: dict[str, str] = {}
    for key, value in old.items():
        for before, after in zip(_escaped(value), _escaped(new[key]), strict=True):
            mapping.setdefault(before, after)
    owners = [s for key, value in old.items() if is_owner(key) for s in spellings(value)]
    pattern = _pattern(mapping, ignore_case=False, owners=owners)
    changed: list[str] = []
    canonical = (root / CANONICAL).read_text(encoding="utf-8")
    for copy in COPIES:
        if _text(root / copy) != canonical:
            (root / copy).write_text(canonical, encoding="utf-8")
            changed.append(copy)
    for name in derived:
        path = root / name
        text = path.read_text(encoding="utf-8")
        updated = pattern.sub(lambda match: mapping[match.group(0)], text)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed.append(name)
    return changed


def left_behind(root: pathlib.Path, old: Mapping[str, str], new: Mapping[str, str]) -> list[str]:
    """Where a value that changed is still spelled as it was, outside history: what a rewrite missed."""
    # A former owner is looked for as a bare name too: after a move it may only stay where a person wrote
    # it in prose, which the check, reading the owner as a path, does not see.
    moved = {f"former {key} name": value for key, value in old.items() if new.get(key) != value}
    if not moved:
        return []
    former = {s.lower() for value in moved.values() for s in spellings(value)}
    # Read with the current identity too: a former value inside a longer current one is the current one
    # (the owner inside an unchanged registry, say), not a value left behind.
    return [
        f"{name}:{number}: `{text}` is the former identity"
        for name, hits in sorted(occurrences(root, {**new, **moved}).items())
        if not is_history(name)
        for number, text in hits
        if text.lower() in former
    ]


def _identity_at(ref: str, root: pathlib.Path) -> dict[str, str]:
    return identity_of(_git(root, "show", f"{ref}:{CANONICAL}").decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--rewrite-from", metavar="REF", help="the commit whose identity the files still spell"
    )
    args = parser.parse_args(argv)
    if args.rewrite_from:
        old, new = _identity_at(args.rewrite_from, ROOT), current_identity(ROOT)
        changed = rewrite(ROOT, old, new)
        print(f"{len(changed)} file(s) rewritten" + "".join(f"\n  {name}" for name in changed))
        issues = left_behind(ROOT, old, new)
    else:
        issues = []
    issues += problems(ROOT)
    for issue in issues:
        print(issue, file=sys.stderr)
    if not issues:
        found = occurrences(ROOT, current_identity(ROOT))
        history = sum(1 for name in found if is_history(name))
        print(
            f"publishing identity: 1 definition, {len(COPIES)} copy, {len(DERIVED)} derived files,", end=" "
        )
        print(f"{history} history files")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
