"""Does the runner image carry rtk pinned, verified, silent, and runnable? (ADR 0055, S37-04)

rtk rewrites what an agent reads from its shell. A binary that is not the one pinned would be a
binary that sees every command of every run, so the image checks it against `versions.lock`.
rtk also carries an HTTP client for its usage telemetry, and the image switches that off.

Building the image is the CI's job (`docker build` with a wrong digest fails at `sha256sum -c`).
These tests keep a later edit from removing the check without anyone noticing.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker/runner.Dockerfile"
LOCK = ROOT / "packages/runner/src/choregos_runner/backends/versions.lock"
STAGE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?\S+(?:\s+AS\s+(\S+))?\s*$", re.M | re.I)


def _stages() -> dict[str, str]:
    text = DOCKERFILE.read_text(encoding="utf-8")
    starts = list(STAGE.finditer(text))
    ends = [start.start() for start in starts[1:]] + [len(text)]
    return {
        start.group(1) or f"#{i}": text[start.end() : end]
        for i, (start, end) in enumerate(zip(starts, ends, strict=True))
    }


def _lock() -> list[str]:
    return LOCK.read_text(encoding="utf-8").splitlines()


def _digest_keys() -> dict[str, str]:
    pins = dict(line.split("=", 1) for line in _lock() if line and not line.startswith("#"))
    return {key: value for key, value in pins.items() if key.startswith("rtk-sha256-")}


def test_versions_lock_pins_rtk_with_renovate_and_its_digests() -> None:
    lines = _lock()
    index = next(i for i, line in enumerate(lines) if line.startswith("rtk="))
    assert re.fullmatch(r"rtk=\d+\.\d+\.\d+", lines[index])
    assert lines[index - 1] == "# renovate: datasource=github-releases depName=rtk-ai/rtk"
    digests = _digest_keys()
    assert set(digests) == {"rtk-sha256-x86_64-unknown-linux-musl", "rtk-sha256-source"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in digests.values()), digests


def test_every_download_is_checked_against_its_digest_before_use() -> None:
    stage = _stages()["rtk"]
    downloads = stage.count("-o rtk.tar.gz")
    for key in _digest_keys():
        assert f'echo "$(version {key})  rtk.tar.gz" | sha256sum -c -' in stage, key
    assert downloads == len(_digest_keys()) == stage.count("sha256sum -c -")
    for branch in (b for b in stage.split(";;") if "-o rtk.tar.gz" in b):
        assert branch.index("sha256sum -c -") < branch.index("tar xzf rtk.tar.gz"), branch
    assert "cargo build --release --locked" in stage


def test_the_runtime_image_runs_rtk_with_its_telemetry_off() -> None:
    runtime = _stages()["runtime"]
    assert "COPY --from=rtk /usr/local/bin/rtk /usr/local/bin/rtk" in runtime
    env = re.search(r"^ENV\s+((?:.*\\\n)*.*)$", runtime, re.M)
    assert env is not None and re.search(r"\bRTK_TELEMETRY_DISABLED=1\b", env.group(1))
    assert re.search(r"^\s*timeout 30 rtk --version\b", runtime, re.M)
