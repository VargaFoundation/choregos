# SPDX-License-Identifier: Apache-2.0
"""The plugin's content as the core ships it: the signed archive `plugins/offline/` locks (S83-07).

The template, its agents, skills and evals are published from the platform's plugin repository; the core
runs its tests against the very bytes it ships offline, so a template that no longer works with this
commit's engine, extension or job step turns this suite red before a release carries it.
"""

from __future__ import annotations

import atexit
import contextlib
from pathlib import Path

from tranibor_core.plugins import opened
from tranibor_core.plugins.sources import read_lock

OFFLINE = Path(__file__).resolve().parents[3] / "plugins" / "offline"
NAME = "investment-research"

_stack = contextlib.ExitStack()
atexit.register(_stack.close)


def _root() -> Path:
    lock = read_lock(OFFLINE)
    assert lock is not None and NAME in lock, f"{NAME} is not in {OFFLINE}/plugins.lock"
    version, digest = lock[NAME]
    plugin = _stack.enter_context(opened(OFFLINE / f"{NAME}-{version}.tar.gz"))
    assert plugin.digest == digest, "the archive is the one the lock names"
    return plugin.root


#: The unpacked content of the locked archive, valid for the whole session.
ROOT = _root()
TEMPLATE = ROOT / "templates" / NAME
