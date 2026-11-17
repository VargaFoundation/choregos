# SPDX-License-Identifier: Apache-2.0
"""S80-02: the install pages tell a reader to install the version the repository is at.

On 2026-11-16 `docs/deployment.md` installed chart 0.4.1 while the chart was at 0.27.8: the release
commit (`chore(release): version X everywhere`, written by `tools/bump_version.py`) rewrote the
pyprojects and the charts, and the `docs/site/` pages by hand, never the deployment guide. The pages
are now part of what `bump_version.py` writes, so a release keeps them true and this test green.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import tomllib
from types import ModuleType

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: Every version an install or upgrade command names: `helm … --version X`, a release wheel's file name.
NAMED = (
    re.compile(r"--version (\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?) "),
    re.compile(r"choregos_[a-z_]+-(\d+\.\d+\.\d+[0-9A-Za-z.]*)-py3-none-any\.whl"),
)
#: The pages a reader installs from, and how many versions each must at least name.
PAGES = {
    "docs/deployment.md": 1,
    "docs/site/host/install.md": 1,
    "docs/site/host/upgrades.md": 1,
    "docs/site/cli/install.md": 5,
    "docs/site/build/agents.md": 2,
}


def _current() -> str:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    assert isinstance(version, str)
    return version


def _bump() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bump_version", ROOT / "tools" / "bump_version.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _named(text: str) -> list[str]:
    return [version for pattern in NAMED for version in pattern.findall(text)]


def test_the_install_pages_name_the_current_version() -> None:
    current, stale = _current(), []
    for page, at_least in PAGES.items():
        named = _named((ROOT / page).read_text("utf-8"))
        assert len(named) >= at_least, f"{page} names {len(named)} versions to install, {at_least} expected"
        stale += [f"{page}: {version}" for version in named if version != current]
    assert not stale, f"install commands that do not name {current}, the repository's version: {stale}"


def test_the_deployment_guide_installs_the_chart_of_the_repository() -> None:
    chart = (ROOT / "charts" / "choregos" / "Chart.yaml").read_text("utf-8")
    version = re.search(r"^version: (\S+)$", chart, re.M)
    assert version, "charts/choregos/Chart.yaml lost its version"
    guide = (ROOT / "docs" / "deployment.md").read_text("utf-8")
    assert f"oci://ghcr.io/graalsystems/charts/choregos \\\n  --version {version.group(1)} " in guide


def test_the_release_bump_rewrites_every_install_page() -> None:
    """What `bump_version.py X` writes for each page leaves no version but X in its commands."""
    bump = _bump()
    targets = {path.relative_to(ROOT).as_posix(): patterns for path, patterns in bump.install_docs()}
    assert set(PAGES) <= set(targets), (
        f"pages the release does not rewrite: {sorted(set(PAGES) - set(targets))}"
    )
    for page in PAGES:
        after = (ROOT / page).read_text("utf-8")
        for pattern, replacement in targets[page]:
            after = pattern.sub(replacement.replace("{v}", "9.8.7"), after)
        left = [version for version in _named(after) if version != "9.8.7"]
        assert not left, f"a release to 9.8.7 would leave {page} naming {left}"
    assert all(path in [p for p, _ in bump._files()] for path, _ in bump.install_docs())
