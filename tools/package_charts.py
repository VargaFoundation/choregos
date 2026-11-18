#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Packages each chart of the release under every name it is published as (ADR 0176 R3).

    uv run python tools/package_charts.py --version 1.2.3 --out dist/charts

`charts/choregos` is published as `<image_prefix>` and `<image_alias_prefix>`, `charts/choregos-outpost`
as `<image_prefix>-outpost` and `<image_alias_prefix>-outpost`: the names come from the publishing
identity (ADR 0156), never from this file. One directory per chart, so one set of templates and
defaults: each name is a copy whose `Chart.yaml` says that name, and nothing else differs. The objects
a chart renders do not take their names from the chart's name, so a deployment that moves from one
name to the other renders the same objects (`tests/charts/test_chart_names.py`).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import tempfile

from choregos_core.publishing import CHARTS, PUBLISHING, Publishing

ROOT = pathlib.Path(__file__).resolve().parents[1]
_NAME = re.compile(r"^name: .+$", re.MULTILINE)


def renamed(chart: pathlib.Path, name: str, into: pathlib.Path) -> pathlib.Path:
    """A copy of `chart` in `into/<name>` whose `Chart.yaml` names it `name`; nothing else changes."""
    copy = into / name
    shutil.copytree(chart, copy)
    manifest = copy / "Chart.yaml"
    text = manifest.read_text(encoding="utf-8")
    updated, count = _NAME.subn(f"name: {name}", text, count=1)
    if count != 1:
        raise ValueError(f"{chart}/Chart.yaml: no top-level `name:` to rename")
    manifest.write_text(updated, encoding="utf-8")
    return copy


def package(
    version: str, out: pathlib.Path, root: pathlib.Path = ROOT, publishing: Publishing = PUBLISHING
) -> list[pathlib.Path]:
    """Every chart of the release, once per name, as `out/<name>-<version>.tgz`."""
    helm = shutil.which("helm")
    if helm is None:
        raise RuntimeError("helm is not installed")
    out.mkdir(parents=True, exist_ok=True)
    packaged: list[pathlib.Path] = []
    for suffix, directory in CHARTS.items():
        for name in publishing.chart_names(suffix):
            with tempfile.TemporaryDirectory() as scratch:
                copy = renamed(root / directory, name, pathlib.Path(scratch))
                subprocess.run(  # noqa: S603 - helm, with a path and a version
                    [
                        helm,
                        "package",
                        str(copy),
                        "--version",
                        version,
                        "--app-version",
                        version,
                        "--destination",
                        str(out),
                    ],
                    check=True,
                    capture_output=True,
                )
            packaged.append(out / f"{name}-{version}.tgz")
    return packaged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", required=True, help="the chart's and the application's version")
    parser.add_argument("--out", required=True, type=pathlib.Path, help="where the archives go")
    args = parser.parse_args(argv)
    for archive in package(args.version.removeprefix("v"), args.out):
        print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
