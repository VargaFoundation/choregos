"""Does every image apply Debian's security fixes when it is built?

The images start from tags (`python:3.12-slim-bookworm`, `node:24-bookworm-slim`) that their
maintainers rebuild at their own pace. On 2026-10-06, `perl-base` 5.36.0-7+deb12u3 carried
three CRITICALs already fixed in `bookworm-security`: the release's Trivy scan rejected four
images out of five, and 0.16.0 did not ship. The runner passed — by luck: `build-essential`
pulled in `perl`, and with it the fixed `perl-base`.

The rule: every stage that ends up in an image — it starts from an external image and is not
merely the source of a `COPY --from` — runs `apt-get upgrade`. A stage that starts from another
stage of the same file inherits it.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"
STAGE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", re.M | re.I)
#: A stage only copied or mounted from (`COPY --from=x`, `RUN --mount=...,from=x`) is a source, not an image.
SOURCE = re.compile(r"(?:COPY\s+--from=|--mount=\S*?\bfrom=)([^\s,]+)")
UPGRADE = re.compile(r"apt-get\s+upgrade\s+-y")


def _final_stages() -> list[tuple[str, str, str]]:
    """(file, stage, body) of every stage that ends up in an image and starts from an external one."""
    finales: list[tuple[str, str, str]] = []
    for path in sorted(DOCKER.glob("*.Dockerfile")):
        text = path.read_text(encoding="utf-8")
        debuts = list(STAGE.finditer(text))
        stages = []
        for i, start in enumerate(debuts):
            end = debuts[i + 1].start() if i + 1 < len(debuts) else len(text)
            stages.append((start.group(2) or f"#{i}", start.group(1), text[start.end() : end]))
        names = {name for name, _, _ in stages}
        bases_internes = {base for _, base, _ in stages if base in names}
        sources = set(SOURCE.findall(text))
        for name, base, body in stages:
            if base in names:
                continue  # it inherits from the stage it starts from
            if name in sources and name not in bases_internes:
                continue  # a build stage: only what is copied from it is kept
            finales.append((path.name, name, body))
    return finales


def test_il_y_a_bien_des_images_a_verifier() -> None:
    """Without this guard, renaming the stages would leave the suite green and empty."""
    vues = {(file, stage) for file, stage, _ in _final_stages()}
    assert {
        ("api.Dockerfile", "commun"),
        ("tools.Dockerfile", "runtime"),
        ("outpost.Dockerfile", "runtime"),
        ("web.Dockerfile", "runtime"),
        ("runner.Dockerfile", "runtime"),
        ("base.Dockerfile", "base"),
    } <= vues, vues


def test_each_image_applies_the_debian_fixes() -> None:
    finales = _final_stages()
    solo = [f"{file} / {stage}" for file, stage, body in finales if not UPGRADE.search(body)]
    assert not solo, f"final stages without `apt-get upgrade -y`: {solo}"
