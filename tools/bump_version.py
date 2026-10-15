#!/usr/bin/env python3
"""Sets ONE version everywhere — or checks it is already there.

    uv run python tools/bump_version.py 0.4.0          # writes
    uv run python tools/bump_version.py 0.4.0 --check  # checks (the release does)

What "everywhere" means: the nine `pyproject.toml`, the umbrella's `Chart.yaml` (its
version, its appVersion, the version of each of its dependencies), each subchart's
`Chart.yaml`, and the image tags (global `imageTag`, the subcharts' `image.tag`).

Before 2026-09-24 nothing did this: the pyprojects stayed at 0.1.0, the chart at 1.0.0,
and `release.yml` rewrote `imageTag` with `sed` at build time — so the repository
permanently held lying versions. Here the version is set BEFORE the tag, and the release
refuses a tag that does not match the files.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


def _files() -> list[tuple[pathlib.Path, list[tuple[re.Pattern[str], str]]]]:
    """(file, [(pattern, replacement-with-\\g<1>VERSION)])."""
    targets: list[tuple[pathlib.Path, list[tuple[re.Pattern[str], str]]]] = []
    for pyproject in sorted(ROOT.glob("**/pyproject.toml")):
        if ".venv" in pyproject.parts or "node_modules" in pyproject.parts:
            continue
        text = pyproject.read_text(encoding="utf-8")
        if not re.search(r'^name = "choregos', text, re.M):
            continue
        targets.append((pyproject, [(re.compile(r'^(version = ")[^"]+(")', re.M), r"\g<1>{v}\g<2>")]))
    umbrella = ROOT / "charts" / "choregos" / "Chart.yaml"
    targets.append(
        (
            umbrella,
            [
                (re.compile(r"^(version: ).*$", re.M), r"\g<1>{v}"),
                (re.compile(r"^(appVersion: ).*$", re.M), r'\g<1>"{v}"'),
                (re.compile(r"^(    version: ).*$", re.M), r"\g<1>{v}"),
            ],
        )
    )
    for chart in sorted((ROOT / "charts" / "choregos" / "charts").glob("*/Chart.yaml")):
        targets.append(
            (
                chart,
                [
                    (re.compile(r"^(version: ).*$", re.M), r"\g<1>{v}"),
                    (re.compile(r"^(appVersion: ).*$", re.M), r'\g<1>"{v}"'),
                ],
            )
        )
    values = ROOT / "charts" / "choregos" / "values.yaml"
    targets.append(
        (
            values,
            [
                (re.compile(r'^(  imageTag: ").*(")$', re.M), r"\g<1>{v}\g<2>"),
                (re.compile(r'^(    tag: ")\d[^"]*(")$', re.M), r"\g<1>{v}\g<2>"),
            ],
        )
    )
    # The OpenAPI is the contract the front end and the CLI consume; its `info.version` said
    # `1.0.0` from day one, while the rest of the repository was at 0.x. A lying version is
    # exactly what this tool exists to prevent.
    openapi = ROOT / "packages" / "contracts" / "openapi.yaml"
    targets.append((openapi, [(re.compile(r"^(  version: ).*$", re.M), r"\g<1>{v}")]))
    # The Claude Code plugin (ADR 0030): its version pins what users install.
    plugin = ROOT / "integrations" / "claude-code" / ".claude-plugin" / "plugin.json"
    targets.append((plugin, [(re.compile(r'^(  "version": ")[^"]+(",)$', re.M), r"\g<1>{v}\g<2>")]))
    return targets


def apply(version: str, *, check: bool) -> int:
    diverging: list[str] = []
    for file, patterns in _files():
        before = file.read_text(encoding="utf-8")
        after = before
        for pattern, replacement in patterns:
            after = pattern.sub(replacement.replace("{v}", version), after)
        if after != before:
            if check:
                diverging.append(str(file.relative_to(ROOT)))
            else:
                file.write_text(after, encoding="utf-8")
                print(f"{file.relative_to(ROOT)} → {version}")
    if check and diverging:
        print(f"version {version} missing from: {', '.join(diverging)}", file=sys.stderr)
        return 1
    if check:
        print(f"version {version} everywhere")
    elif any("Chart.yaml" in str(f) for f, _ in _files()):
        print("→ then: helm dependency update charts/choregos  (Chart.lock)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version")
    parser.add_argument("--check", action="store_true", help="write nothing, fail if a file diverges")
    args = parser.parse_args()
    if not SEMVER.match(args.version):
        parser.error(f"semver version expected, got {args.version!r}")
    return apply(args.version, check=args.check)


if __name__ == "__main__":
    sys.exit(main())
