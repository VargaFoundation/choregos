"""The web image's builder stage holds every file the front end imports from outside `apps/web`.

`pnpm build` type-checks `apps/web/tests` too, so an import that resolves on a checkout but points
at a directory the builder stage never copies breaks the image while `make web-ci` stays green.
S42-15 did exactly that: a test imported `packages/contracts/schemas/ui-manifest.schema.json`
while the stage copied only `packages/contracts/ts` (finding #1016).
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
DOCKERFILE = ROOT / "docker" / "web.Dockerfile"

_IMPORT = re.compile(r"""(?:from|import)\s*\(?\s*["']((?:\.\./)+[^"']+)["']""")


def _builder_copies() -> list[pathlib.Path]:
    stages = re.split(r"^FROM ", DOCKERFILE.read_text(encoding="utf-8"), flags=re.M)
    [stage] = [stage for stage in stages if re.match(r"\S+ AS builder\n", stage)]
    sources: list[pathlib.Path] = []
    for line in stage.splitlines():
        parts = line.split()
        if parts[:1] == ["COPY"] and not any(part.startswith("--from") for part in parts):
            sources += [ROOT / source.rstrip("*") for source in parts[1:-1]]
    return sources


def _imports_leaving_web() -> list[tuple[pathlib.Path, pathlib.Path]]:
    found = []
    for path in [*WEB.glob("src/**/*.ts*"), *WEB.glob("tests/**/*.ts*")]:
        for target in _IMPORT.findall(path.read_text(encoding="utf-8")):
            resolved = (path.parent / target).resolve()
            if not resolved.is_relative_to(WEB):
                found.append((path, resolved))
    return found


def test_the_web_front_end_imports_from_outside_apps_web() -> None:
    # Without such an import the next test would pass vacuously.
    assert _imports_leaving_web()


def test_every_import_from_outside_apps_web_is_copied_into_the_builder_stage() -> None:
    copied = _builder_copies()
    missing = [
        f"{path.relative_to(ROOT)} -> {target.relative_to(ROOT)}"
        for path, target in _imports_leaving_web()
        if not any(target == source or target.is_relative_to(source) for source in copied)
    ]
    assert not missing, f"the web image's builder stage does not copy: {missing}"
