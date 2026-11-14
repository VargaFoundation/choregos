---
title: Install the CLI
description: Get the choregos command from your instance or from the GitHub release, sign in, and keep it up to date.
sidebar_position: 1
---

# Install the CLI

The `choregos` command drives your instance from a terminal or a script: projects, workflows, work
items, tokens, applications, the A2A door, plugins. It calls the same API as the console, with your
rights.

**What you will be able to do:** install the CLI, check what you downloaded, sign in to your instance,
and keep the CLI at your instance's version.

**Before you start:** the address of your Choregos instance (here `https://choregos.example.com`) and
an account on it.

## From your instance

:::info Available from the release that follows 0.25.3
The downloads served by the instance, the install scripts and `choregos self update` arrive with the
release that follows 0.25.3. On 0.25.3 and earlier, install from the GitHub release
([below](#from-the-github-release)).
:::

Your instance serves the CLI built for its own version, as a single native file for Linux, macOS and
Windows: **no Python is needed**. The console's **Integrations** page, tab **CLI**, shows the commands
below with your instance's address filled in.

**Linux and macOS:**

```bash
curl -fsSL https://choregos.example.com/cli/install.sh | sh
```

**Windows (PowerShell):**

```powershell
irm https://choregos.example.com/cli/install.ps1 | iex
```

The script picks the file for your system, checks its SHA-256 against the instance's manifest, and
puts `choregos` on your path.

**By hand**, download the file for your system from your instance:

| System | File |
| --- | --- |
| Linux, x86-64 | `choregos-linux-x86_64` |
| Linux, ARM64 | `choregos-linux-arm64` |
| macOS, Apple silicon | `choregos-macos-arm64` |
| macOS, Intel | `choregos-macos-x86_64` |
| Windows, x86-64 | `choregos-windows-x86_64.exe` |

```bash
curl -fsSLO https://choregos.example.com/downloads/cli/choregos-linux-x86_64
curl -fsSL https://choregos.example.com/downloads/cli/manifest.json
```

`manifest.json` gives the version and, for each file, its system, architecture, size and `sha256`.
Compare it with the file you downloaded:

```shell
sha256sum choregos-linux-x86_64
chmod +x choregos-linux-x86_64 && mv choregos-linux-x86_64 ~/.local/bin/choregos
```

On macOS, `shasum -a 256` gives the same digest. The same files, a `SHA256SUMS` file and their
signatures are also attached to each
[GitHub release](https://github.com/VargaFoundation/choregos/releases), under the same names.

An instance built without the CLI says so on its Integrations page; ask your operator, or use the
GitHub release.

## Keep it at your instance's version

The CLI tells you when its version differs from your instance's. Replace it with the instance's own
build:

```shell
choregos self update
```

## From the GitHub release

This works on every version, and is the way on 0.25.3 and earlier. It needs Python 3.12 and
[uv](https://docs.astral.sh/uv/). Download three wheels of your instance's version from its
[GitHub release](https://github.com/VargaFoundation/choregos/releases) (here 0.27.3):
`choregos_cli-0.27.3-py3-none-any.whl`, `choregos_core-0.27.3-py3-none-any.whl` and
`choregos_contracts-0.27.3-py3-none-any.whl`. Then:

```shell
uv tool install --python 3.12 ./choregos_cli-0.27.3-py3-none-any.whl \
  --with ./choregos_core-0.27.3-py3-none-any.whl --with ./choregos_contracts-0.27.3-py3-none-any.whl
```

To upgrade, install the next version's
wheels the same way with `--force`.

## Sign in

The CLI cannot issue its first token: create a full-access token in the console, menu under your
name, **My account**, *Access tokens*, **Create an API token (90 days)**. Then:

```bash
choregos login --api-url https://choregos.example.com --token chg_... --org acme
choregos whoami
```

**What you should see:** `whoami` prints who you are and your roles. The profile is saved in
`~/.config/choregos/config.json` (`CHOREGOS_CONFIG` names another file). Without a profile, the CLI
reads `CHOREGOS_API_URL`, `CHOREGOS_TOKEN` and `CHOREGOS_ORG`, which suits a CI job.

## What can go wrong

| Symptom | Why | Fix |
| --- | --- | --- |
| `cannot reach http://localhost:8000` | no profile yet | `choregos login --api-url …` |
| `401` | the token is unknown, revoked or expired | create a new one in **My account** |
| `403` on every command | an MCP or A2A token in the profile | the CLI needs a full-access (`*`) token |
| `no organisation` | neither `--org`, the profile nor `CHOREGOS_ORG` names one | `choregos login --org acme`, or `choregos orgs list` |
| the digest does not match | an incomplete or altered download | download again; do not run the file |
| `Windows refused to run` (or `refused access to`) `the downloaded choregos.exe` | a managed Windows: Defender's attack surface reduction blocks an executable that is new and not yet widespread or signed | ask your IT administrator for an exclusion of the install folder, or install [from the GitHub release](#from-the-github-release) |

## Go further

- [CLI reference](reference.md): every command and option.
- [Start a workflow over A2A](../integrate/a2a.md) with `choregos a2a`.
