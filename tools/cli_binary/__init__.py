"""The `choregos` CLI as native, self-contained binaries (S62-07): build, smoke test, release files.

Each module is also a script that needs only the standard library, so a release runner (or a
person's machine) runs it with any Python 3.12 before the workspace is installed:

    python tools/cli_binary/build.py              # the binary of this machine, in dist/cli/
    python tools/cli_binary/smoke.py dist/cli/choregos-linux-x86_64 --expect-version 0.25.3
    python tools/cli_binary/release.py dist/cli   # SHA256SUMS and manifest.json beside them
"""
