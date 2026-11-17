# SPDX-License-Identifier: Apache-2.0
"""S80-01: no stream is opened after 2026-11-20 other than S80 (ADR 0180).

The freeze is a list: the streams that existed on 2026-11-17, S76 (open in review that day) and
S80. A story of any other stream, in the backlog or in STATUS, fails here. Opening a stream is an
amendment of ADR 0180 by the project lead, and the PR that carries it adds the stream below.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "plan"
BACKLOG = PLAN / "08-backlog.yaml"
STREAM = re.compile(r"^S\d+$")

#: S53 and S54 were never opened; S76 was in review on 2026-11-17; S80 carries the roadmap.
ALLOWED = frozenset(
    [f"S{n}" for n in range(0, 53)] + [f"S{n}" for n in range(55, 77)] + ["S80"],
)


def _backlog_streams() -> set[str]:
    data = yaml.safe_load(BACKLOG.read_text("utf-8"))
    declared = set(data["streams"])
    used = {story["stream"] for story in data["stories"]}
    return {stream for stream in declared | used if STREAM.match(stream)}


def _status_streams() -> set[str]:
    """The streams of the rows of `docs/plan/status/<stream>.md`, read by `tools/status_total.py`."""
    spec = importlib.util.spec_from_file_location("status_total", ROOT / "tools" / "status_total.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {row.stream for row in module.rows() if STREAM.match(row.stream)}


def test_the_backlog_opens_no_stream_beyond_the_freeze() -> None:
    streams = _backlog_streams()
    assert "S80" in streams, "the backlog has no S80 stream: the test reads the wrong file"
    opened = sorted(streams - ALLOWED, key=lambda s: int(s[1:]))
    assert not opened, f"streams opened after the freeze of ADR 0180 (amend it first): {opened}"


def test_status_opens_no_stream_beyond_the_freeze() -> None:
    streams = _status_streams()
    assert len(streams) > 30, "too few streams found in STATUS: its rows changed shape"
    opened = sorted(streams - ALLOWED, key=lambda s: int(s[1:]))
    assert not opened, f"STATUS rows of streams opened after the freeze of ADR 0180: {opened}"
