"""The native CLI binary's build, smoke test and release files (S62-07), without building one.

Building takes minutes and a C compiler: the `cli-binary` CI job and the release do it. Here, what can
go wrong without a build: the five fixed file names (a shared contract with S62-08), the manifest's
shape, what the Nuitka command puts in and leaves out, and the smoke test refusing what is not a
self-contained binary.
"""

from __future__ import annotations

import fnmatch
import json
import os
import pathlib
import stat
import sys
import urllib.request

import pygments.lexers
import pytest
from choregos_cli import a2a

from tools.cli_binary import build, release, smoke

CONTRACT = {
    "choregos-linux-x86_64": ("linux", "x86_64"),
    "choregos-linux-arm64": ("linux", "arm64"),
    "choregos-macos-arm64": ("macos", "arm64"),
    "choregos-macos-x86_64": ("macos", "x86_64"),
    "choregos-windows-x86_64.exe": ("windows", "x86_64"),
}


def test_the_five_file_names_are_the_contract_s() -> None:
    assert {name: target for target, name in build.TARGETS.items()} == CONTRACT
    assert release.TARGETS == CONTRACT


def test_this_machine_has_a_target() -> None:
    assert build.file_name(*build.host_target()) in CONTRACT


@pytest.mark.parametrize(
    ("version", "numeric"), [("0.25.3", "0.25.3.0"), ("1.2.3-rc.1", "1.2.3.0"), ("10.0.12", "10.0.12.0")]
)
def test_windows_file_version_is_four_numbers(version: str, numeric: str) -> None:
    assert build.numeric_version(version) == numeric


def test_the_build_s_version_is_the_cli_s() -> None:
    from choregos_cli import __version__

    assert build.cli_version() == __version__


def test_the_nuitka_command_carries_the_data_and_leaves_out_what_is_not_the_cli(
    tmp_path: pathlib.Path,
) -> None:
    command = build.nuitka_command(pathlib.Path("python"), tmp_path, "choregos-linux-x86_64", "0.25.3")
    assert "--mode=onefile" in command
    for package in ("choregos_core", "choregos_contracts", "jsonschema_specifications"):
        assert f"--include-package-data={package}" in command
    assert "--include-distribution-metadata=choregos-cli" in command
    for module in ("opentelemetry", "choregos_orchestrator", "zstandard"):
        assert f"--nofollow-import-to={module}" in command
    spec = next(arg for arg in command if arg.startswith("--onefile-tempdir-spec="))
    assert spec.startswith("--onefile-tempdir-spec={CACHE_DIR}/choregos/cli-0.25.3-")
    assert command[-1] == str(build.ENTRY)


def test_the_pygments_patterns_keep_the_python_lexer_and_the_name_table_only() -> None:
    """Rich highlights a traceback with the Python lexer, found through `_mapping`: those two stay."""
    folder = pathlib.Path(pygments.lexers.__file__).parent
    modules = sorted(p.stem for p in folder.glob("*.py") if p.stem != "__init__")
    kept = [m for m in modules if not any(fnmatch.fnmatch(m, pat) for pat in build.PYGMENTS_LEFT_OUT)]
    assert kept == ["_mapping", "python"]


def test_forbidden_modules_are_found_in_a_report(tmp_path: pathlib.Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        '<nuitka-compilation-report><module name="choregos_cli.main"/><module name="opentelemetry.trace"/>'
        '<module name="opentelemetry_like"/></nuitka-compilation-report>',
        encoding="utf-8",
    )
    modules = build.bundled_modules(report)
    assert build.forbidden_in(modules) == ["opentelemetry.trace"]


def test_the_report_names_the_metadata_the_binary_carries(tmp_path: pathlib.Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        '<nuitka-compilation-report><included_metadata name="choregos-cli" reason="user requested"/>'
        '<included_metadata name="attrs" reason="static analysis"/></nuitka-compilation-report>',
        encoding="utf-8",
    )
    assert build.bundled_metadata(report) == ["attrs", "choregos-cli"]
    assert set(build.REQUIRED_METADATA) <= set(build.bundled_metadata(report))
    for name in build.REQUIRED_METADATA:
        command = build.nuitka_command(pathlib.Path("python"), tmp_path, "choregos-linux-x86_64", "0.25.3")
        assert f"--include-distribution-metadata={name}" in command


def test_the_linux_build_runs_in_the_manylinux_2_28_image(tmp_path: pathlib.Path) -> None:
    if build.shutil.which("docker") is None:
        pytest.skip("docker missing")
    command = build.container_command(tmp_path / "out", build.MANYLINUX["x86_64"])
    assert "quay.io/pypa/manylinux_2_28_x86_64:" in command[-3]
    assert command[-1].endswith("in_container.sh")
    assert f"--env=PYTHON={build.MANYLINUX_PYTHON}" in command


# ───────────────────────────── release files ─────────────────────────────


def _binaries(directory: pathlib.Path, names: list[str]) -> None:
    for name in names:
        (directory / name).write_bytes(name.encode())


def test_the_manifest_and_the_sums(tmp_path: pathlib.Path) -> None:
    _binaries(tmp_path, list(CONTRACT))
    doc = release.write(tmp_path, "0.25.3")
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == doc
    assert manifest["version"] == "0.25.3"
    assert [f["name"] for f in manifest["files"]] == list(CONTRACT)
    for entry in manifest["files"]:
        assert set(entry) == {"name", "os", "arch", "sha256", "size"}
        assert (entry["os"], entry["arch"]) == CONTRACT[entry["name"]]
        assert entry["size"] == len(entry["name"])
        assert entry["sha256"] == release.sha256(tmp_path / entry["name"])
    sums = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert sums == [f"{e['sha256']}  {e['name']}" for e in manifest["files"]]


def test_a_release_without_all_five_binaries_is_refused(tmp_path: pathlib.Path) -> None:
    _binaries(tmp_path, ["choregos-linux-x86_64"])
    with pytest.raises(ValueError, match="binaries missing"):
        release.write(tmp_path, "0.25.3")
    assert release.write(tmp_path, "0.25.3", partial=True)["files"][0]["name"] == "choregos-linux-x86_64"
    with pytest.raises(ValueError, match="not a release version"):
        release.write(tmp_path, "v0.25", partial=True)


# ───────────────────────────── smoke test ─────────────────────────────


def test_the_fake_door_serves_the_card_the_cli_reads() -> None:
    assert smoke.SKILL_FIELDS_EXTENSION == a2a.SKILL_FIELDS_EXTENSION
    with smoke.fake_door() as port:
        endpoint = f"http://127.0.0.1:{port}/a2a/orgs/{smoke.ORG}/.well-known/agent-card.json"
        request = urllib.request.Request(endpoint, headers={"Authorization": f"Bearer {smoke.TOKEN}"})
        with urllib.request.urlopen(request) as response:
            card = json.loads(response.read())
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(endpoint)
    assert refused.value.code == 401
    skills = a2a.skills_of(card)
    assert [s["id"] for s in skills] == ["smoke/triage", "smoke/release-notes"]
    assert a2a.describe_fields(skills[0]["fields"]).startswith("ticket* (string)")


def test_the_bare_environment_has_no_python(tmp_path: pathlib.Path) -> None:
    env = smoke.bare_environment(tmp_path, "http://127.0.0.1:9")
    smoke.no_python_on(env["PATH"])
    assert not any(k.startswith("PYTHON") and k not in {"PYTHONHOME", "PYTHONPATH"} for k in env)
    with pytest.raises(smoke.SmokeError, match="a Python is on the binary's PATH"):
        smoke.no_python_on(str(pathlib.Path(sys.executable).parent))


@pytest.mark.skipif(os.name == "nt", reason="a shebang script stands for the binary")
def test_a_cli_that_needs_a_python_fails_the_smoke_test(tmp_path: pathlib.Path) -> None:
    """The installed CLI's console script is a Python program: in the smoke test's environment it
    cannot start, so a 'binary' that is a Python in disguise never passes."""
    fake = tmp_path / "choregos-linux-x86_64"
    fake.write_text(f"#!{sys.executable}\nfrom choregos_cli.main import main\nmain()\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(smoke.SmokeError, match="--version"):
        smoke.smoke(fake, "0.25.3")


def test_expect_reads_the_exit_code_and_the_output() -> None:
    result = smoke.Result(code=0, out="choregos 0.25.3\n", err="", seconds=0.1)
    smoke.expect(result, 0, "choregos 0.25.3", what="--version")
    with pytest.raises(smoke.SmokeError, match="exit 0, expected 1"):
        smoke.expect(result, 1, what="a broken workflow")
    with pytest.raises(smoke.SmokeError, match="not in the output"):
        smoke.expect(result, 0, "choregos 9.9.9", what="--version")


@pytest.mark.parametrize(
    ("warm", "max_size_mb", "failure"),
    [(5.0, 40.0, "over the 2.0 s budget"), (0.1, 0.000001, "MB budget")],
)
def test_the_budgets_are_enforced(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, warm: float, max_size_mb: float, failure: str
) -> None:
    binary = tmp_path / "choregos-linux-x86_64"
    binary.write_bytes(b"x" * 100)
    monkeypatch.setattr(smoke, "timings", lambda runner, version: (0.2, warm))
    monkeypatch.setattr(smoke, "functional_checks", lambda runner, version: None)
    monkeypatch.setattr(smoke, "self_knowledge_checks", lambda runner, version: [])
    with pytest.raises(smoke.SmokeError, match=failure):
        smoke.smoke(binary, "0.25.3", max_size_mb=max_size_mb, max_warm_start_s=2.0)
    monkeypatch.setattr(smoke, "timings", lambda runner, version: (0.2, 0.1))
    report = smoke.smoke(binary, "0.25.3", max_size_mb=40.0, max_warm_start_s=2.0)
    assert report["size_bytes"] == 100 and report["warm_start_s"] == 0.1


def _info(**overrides: object) -> str:
    info = {
        "version": "0.25.3",
        "metadata_version": "0.25.3",
        "compiled": True,
        "argv0": "choregos",
        "running_binary": None,
        "detector": "choregos_cli.binary_info",
    }
    info.update(overrides)
    return json.dumps(info)


@pytest.mark.parametrize(
    ("overrides", "failure"),
    [
        ({}, None),
        ({"compiled": False}, "not a Nuitka build"),
        ({"metadata_version": None, "version": "0.0.0+unknown"}, "left the distribution metadata out"),
        ({"version": "0.25.3-hard-coded"}, "does not come from the metadata"),
        ({"running_binary": "/tmp/elsewhere/choregos"}, "the running binary resolves to"),
    ],
)
def test_the_binary_must_know_its_version_and_its_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object], failure: str | None
) -> None:
    """What S62-08's version check and `self update` rely on, read from `choregos binary-info`."""
    binary = tmp_path / "choregos-linux-x86_64"
    binary.write_bytes(b"x")
    runner = smoke.Runner(binary, tmp_path, {"PATH": str(tmp_path)})
    real = os.path.normcase(str(binary.resolve()))
    answer = _info(**{"running_binary": real, **overrides})
    monkeypatch.setattr(runner, "run", lambda *args, by_name=False: smoke.Result(0, answer, "", 0.1))
    if failure is None:
        assert len(smoke.self_knowledge_checks(runner, "0.25.3")) == 2
    else:
        with pytest.raises(smoke.SmokeError, match=failure):
            smoke.self_knowledge_checks(runner, "0.25.3")


def test_binary_info_from_python_packages_says_so() -> None:
    """Outside a binary the hidden command reports no running binary: `self update` refuses there."""
    from choregos_cli.binary_info import info

    found = info()
    assert found["compiled"] is False and found["running_binary"] is None
    assert found["version"] == found["metadata_version"] == build.cli_version()
