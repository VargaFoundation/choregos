"""Does the procurement job image install exactly what `uv.lock` locks, by hash? (ADR 0202, S85-09)

The image installs `tranibor-procurement`'s dependencies into `/opt/procurement` from
`plugins/procurement/requirements.txt`, with `--require-hashes`. That file is not written by hand: it is
what `make procurement-lock` exports from `uv.lock`. A dependency bumped in the lock without the export
would leave the image on the old wheel, so the file must equal the export, and the Dockerfile must install
it by hash and keep the runner's environment untouched.

Building the image is the CI's job (`.github/workflows/ci.yml`, the `images` job).
"""

from __future__ import annotations

import pathlib
import re
import shlex
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "procurement"
LOCK = PLUGIN / "requirements.txt"
DOCKERFILE = PLUGIN / "Dockerfile"


def _make_command() -> list[str]:
    """The `uv export` of `make procurement-lock`, with its output file left out (the test reads stdout)."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8").replace("\\\n", " ")
    target = re.search(r"^procurement-lock:.*\n\t\$\(UV\) (.+)$", makefile, re.M)
    assert target is not None, "the Makefile has no `procurement-lock` target"
    words = shlex.split(target.group(1))
    assert "-o" in words and words[words.index("-o") + 1] == "plugins/procurement/requirements.txt"
    index = words.index("-o")
    return ["uv", *words[:index], *words[index + 2 :]]


def _requirements() -> list[str]:
    """Each requirement of the file, with its continuation lines joined."""
    text = LOCK.read_text(encoding="utf-8").replace("\\\n", " ")
    return [line for line in text.splitlines() if line and not line.lstrip().startswith("#")]


def test_the_requirements_are_what_the_lock_exports() -> None:
    assert shutil.which("uv") is not None, "uv is the toolchain of this repository"
    exported = subprocess.run(
        [*_make_command(), "--offline"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert exported.returncode == 0, exported.stderr[-3000:]
    assert LOCK.read_text(encoding="utf-8") == exported.stdout, "run `make procurement-lock` and commit"


def test_every_requirement_is_pinned_with_a_hash() -> None:
    requirements = _requirements()
    names = {line.split("==")[0] for line in requirements}
    assert {"markitdown", "openpyxl"} <= names, "the two direct dependencies of the distribution"
    for line in requirements:
        assert re.match(r"^[A-Za-z0-9._-]+(\[[^\]]+\])?==\S+ ", line), line
        assert re.search(r"--hash=sha256:[0-9a-f]{64}", line), line
    assert not [line for line in requirements if "tranibor" in line], "the workspace's own code is the wheel"


def test_the_dockerfile_installs_them_by_hash_into_an_environment_of_its_own() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    assert "COPY plugins/procurement/requirements.txt /tmp/procurement-requirements.txt" in text
    install = re.search(
        r"uv pip install (--require-hashes [^;]+?-r /tmp/procurement-requirements\.txt)", text
    )
    assert install is not None and "--no-deps" in install.group(1)
    assert "uv venv /opt/procurement --python /usr/local/bin/python3.12" in text
    wheel = re.search(r"uv pip install (--no-deps [^;]*\.whl)", text)
    assert wheel is not None, "the wheel is installed without its dependencies, which the lock gave"
    assert "ln -sf /opt/procurement/bin/tranibor-procurement /usr/local/bin/tranibor-procurement" in text
    # Nothing is installed into the runner's environment.
    installs = re.findall(r"uv pip install [^;]*", text)
    assert installs and all("/app/.venv" not in line for line in installs)
    assert "/opt/procurement" in text and "VIRTUAL_ENV=/opt/procurement" in text


def test_the_build_proves_the_runner_environment_is_untouched() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    before = text.index("uv pip freeze --python /app/.venv/bin/python > /tmp/runner-before.txt")
    install = text.index("uv pip install --require-hashes")
    after = text.index("uv pip freeze --python /app/.venv/bin/python > /tmp/runner-after.txt")
    compare = text.index("cmp /tmp/runner-before.txt /tmp/runner-after.txt")
    assert before < install < after < compare
