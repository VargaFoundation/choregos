"""The CLI suite never reads or writes the developer's real home (#1777)."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

from tranibor_cli import client


def test_the_home_and_the_profile_are_not_the_machines(tmp_path: Path) -> None:
    real = Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert Path.home() != real
    assert client.CONFIG_PATH.is_relative_to(tmp_path)
    assert not client.CONFIG_PATH.exists()
    for name in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        assert Path(os.environ[name]).is_relative_to(Path.home())
