# 0133 — The CLI ships as native binaries

- **Status**: accepted, 2026-11-11; S62-07 implements it, S62-08 serves the files from the instance
- **Concerns**: `packages/cli`, `packages/core` (its `telemetry` extra), `tools/cli_binary/`,
  `release.yml`, `docker/api.Dockerfile`

## Context

The `choregos` CLI is a Python package (typer, rich, httpx, PyYAML) that validates a workflow offline
with `choregos_core`. Nothing distributed it: the release attached wheels, nothing was on PyPI, and a
person had to install Python 3.12 and the workspace to run `choregos --version`. Rewriting it in
another language would drop the local validation, which is the core's own parser.

`choregos-core` also pulled the OpenTelemetry SDK and its OTLP exporter, exact pins, for the spans of
the API, the orchestrator and the runner — none of which the CLI emits.

## Decision

1. **One self-contained executable per target, built with Nuitka** (`--mode=onefile`, standalone
   inside): Linux x86_64 and arm64, macOS arm64 and x86_64, Windows x86_64, under fixed, version-less
   names (`choregos-linux-x86_64`, …, `choregos-windows-x86_64.exe`), with `SHA256SUMS` and a
   `manifest.json`. Each is built on a runner of its own OS and architecture: Nuitka does not
   cross-compile.
2. **The Linux binaries are built in the PyPA `manylinux_2_28` image**, so they run from glibc 2.28
   (RHEL 8, Debian 10, Ubuntu 18.10) on, and link that image's static libpython.
3. **The binary holds what the CLI needs and nothing else.** It is built from a fresh environment with
   the CLI's dependencies pinned by `uv.lock`; OpenTelemetry becomes the `telemetry` extra of
   `choregos-core`, declared by the three services that trace, and `choregos_core.telemetry` turns
   into no-ops without it. The build fails if Nuitka's report names OpenTelemetry or the server.
4. **A binary is released only if it passed its smoke test where no Python is available**: version,
   templates, a valid and two broken workflows, an A2A card read from a fake door, a size and a
   start-up budget. The CI runs the same on the Linux x86_64 binary for every change to the CLI or
   the core.
5. **Every file is signed with cosign (keyless, the release workflow's identity)** and attached to the
   GitHub release; the API image carries the same files in `/opt/choregos/cli/`. Authenticode and
   Apple notarization run when their certificates exist as repository secrets.

## Consequences

- A user downloads one file and runs it; the first call unpacks it into the user's cache directory.
- A dependency Nuitka cannot follow, or a data file the CLI reads at run time and the build leaves
  out, fails a pull request, not a release.
- Until the certificates exist (BLOCKERS, owner: the Varga Foundation), Windows SmartScreen warns and
  a macOS browser download is quarantined.
- The release's images wait for the five binaries: a target that does not build stops the release.
