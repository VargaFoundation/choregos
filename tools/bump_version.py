#!/usr/bin/env python3
"""Sets ONE version everywhere — or checks it is already there.

    uv run python tools/bump_version.py 0.4.0          # writes
    uv run python tools/bump_version.py 0.4.0 --check  # checks (the release does)

What "everywhere" means: the nine `pyproject.toml`, the umbrella's `Chart.yaml` (its
version, its appVersion, the version of each of its dependencies), each subchart's
`Chart.yaml`, the outpost chart's, the image tags (global `imageTag`, the subcharts'
`image.tag`), and the versions the install pages tell a reader to install (`install_docs`).

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
        if not re.search(r'^name = "(choregos|tranibor)', text, re.M):
            continue
        patterns = [(re.compile(r'^(version = ")[^"]+(")', re.M), r"\g<1>{v}\g<2>")]
        if 'name = "tranibor-agent-sdk"' in text:
            # The SDK is published beside the contracts it validates against: same version, pinned.
            patterns.append((re.compile(r'("tranibor-contracts==)[^"]+(")'), r"\g<1>{v}\g<2>"))
        if re.search(r'^name = "choregos-', text, re.M) and "compat" in pyproject.parts:
            # An old distribution (ADR 0176 R2) is an empty wheel that pins the new one it aliases.
            patterns.append((re.compile(r'("tranibor-[a-z-]+==)[^"]+(")'), r"\g<1>{v}\g<2>"))
        targets.append((pyproject, patterns))
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
    subcharts = sorted((ROOT / "charts" / "choregos" / "charts").glob("*/Chart.yaml"))
    # The outpost's chart is a chart of its own, published beside the umbrella (S65-16). Its image tag
    # is its appVersion: no value of it holds a version.
    for chart in [*subcharts, ROOT / "charts" / "choregos-outpost" / "Chart.yaml"]:
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
    targets.extend(install_docs())
    return targets


#: A chart version after `--version` (semver, a pre-release allowed), and a wheel's (PEP 440, no hyphen).
_CHART_VERSION = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?"
_WHEEL_VERSION = r"\d+\.\d+\.\d+[0-9A-Za-z.]*"


def install_docs() -> list[tuple[pathlib.Path, list[tuple[re.Pattern[str], str]]]]:
    """The pages whose install and upgrade commands name the release (S80-02).

    `docs/deployment.md` told an operator to install chart 0.4.1 while the chart was at 0.27: the
    release rewrote the files above and nothing else, and the `docs/site/` pages were edited by hand in
    each release commit. They follow the release here, and `tests/docs/test_install_version.py`
    refuses a page whose version is not the repository's.
    """
    helm = (re.compile(rf"(--version ){_CHART_VERSION}(?= )"), r"\g<1>{v}")
    wheel = (
        re.compile(rf"((?:choregos|tranibor)_[a-z_]+-){_WHEEL_VERSION}(-py3-none-any\.whl)"),
        r"\g<1>{v}\g<2>",
    )
    docs = ROOT / "docs"
    return [
        (
            docs / "deployment.md",
            [
                helm,
                (
                    re.compile(rf'(\{{"edition":"community","features":\[\],"version":"){_CHART_VERSION}(")'),
                    r"\g<1>{v}\g<2>",
                ),
            ],
        ),
        (docs / "site" / "host" / "install.md", [helm]),
        (docs / "site" / "host" / "upgrades.md", [helm]),
        (
            docs / "site" / "cli" / "install.md",
            [wheel, (re.compile(rf"(\(here ){_WHEEL_VERSION}(\))"), r"\g<1>{v}\g<2>")],
        ),
        (docs / "site" / "build" / "agents.md", [wheel]),
    ]


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
