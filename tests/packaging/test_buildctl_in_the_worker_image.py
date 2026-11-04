"""The worker image carries the `buildctl` of the daemon the chart deploys (S41-13).

The build activity runs `buildctl` from the executor worker: without it in the image, every zip
build fails with "No such file or directory" on a cluster, and nothing before that says so. The
client is copied from the very image the chart runs as `buildkitd`, so the two move together.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker" / "api.Dockerfile"
VALUES = ROOT / "charts" / "choregos" / "values.yaml"


def _stage(name: str) -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    start = re.search(rf"^FROM\s+\S+\s+AS\s+{name}\s*$", text, re.M | re.I)
    assert start, f"the api Dockerfile has a `{name}` stage"
    end = re.search(r"^FROM\s", text[start.end() :], re.M)
    return text[start.end() : start.end() + end.start()] if end else text[start.end() :]


def test_the_worker_copies_buildctl_from_the_image_the_chart_runs_as_buildkitd() -> None:
    copied = re.findall(
        r"^COPY\s+--from=(\S+)\s+/usr/bin/buildctl\s+/usr/local/bin/buildctl\s*$", _stage("worker"), re.M
    )
    image = yaml.safe_load(VALUES.read_text(encoding="utf-8"))["global"]["buildkitd"]["image"]
    assert copied == [image], "the worker's client and the chart's daemon are one release, by digest"
    assert "@sha256:" in image
