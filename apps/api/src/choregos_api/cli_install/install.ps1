# SPDX-License-Identifier: Apache-2.0
#
# Installs the `choregos` CLI from a Choregos instance, in the version that instance runs.
#
#   irm __CHOREGOS_INSTANCE_URL__/cli/install.ps1 | iex
#
# Windows x86_64 (an ARM64 Windows runs it under emulation). It downloads the binary from the same
# instance, checks its sha256 against the instance's manifest, installs it as
# %LOCALAPPDATA%\Programs\choregos\choregos.exe and adds that folder to the user's PATH. No
# administrator rights. PowerShell 5.1 or later.
#
# Environment, read when set:
#   CHOREGOS_INSTALL_DIR   the folder to install into (default %LOCALAPPDATA%\Programs\choregos)
#   CHOREGOS_URL           the instance to install from (default: the one that served this script)
#
# It stops with an error rather than `exit`: run through `iex`, `exit` would close the window.

function Install-Choregos {
    [CmdletBinding()]
    param(
        [string]$InstanceUrl = '__CHOREGOS_INSTANCE_URL__',
        [string]$InstallDir = ''
    )
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $releases = 'https://github.com/VargaFoundation/choregos/releases'
    if ($env:CHOREGOS_URL) { $InstanceUrl = $env:CHOREGOS_URL }
    $InstanceUrl = $InstanceUrl.TrimEnd('/')
    if (-not $InstallDir) {
        if ($env:CHOREGOS_INSTALL_DIR) {
            $InstallDir = $env:CHOREGOS_INSTALL_DIR
        } else {
            $InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\choregos'
        }
    }

    # Windows PowerShell 5.1 may still offer TLS 1.0 first.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    if ($arch -ne 'AMD64' -and $arch -ne 'ARM64') {
        throw "choregos install: unsupported processor $arch; the Windows CLI is built for x86_64"
    }
    $name = 'choregos-windows-x86_64.exe'

    $work = Join-Path ([IO.Path]::GetTempPath()) ('choregos-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        # The manifest: the version, and the checksum of this machine's binary.
        try {
            $manifest = Invoke-RestMethod -UseBasicParsing -Uri "$InstanceUrl/downloads/cli/manifest.json"
        } catch {
            throw "choregos install: cannot read $InstanceUrl/downloads/cli/manifest.json ($($_.Exception.Message)); this instance may bundle no CLI, see $releases"
        }
        $entry = $manifest.files | Where-Object { $_.name -eq $name } | Select-Object -First 1
        if (-not $entry) {
            throw "choregos install: this instance's CLI $($manifest.version) has no Windows binary ($name); see $releases"
        }
        $expected = ([string]$entry.sha256).ToLowerInvariant()
        if ($expected -notmatch '^[0-9a-f]{64}$') {
            throw "choregos install: the manifest gives no valid sha256 for $name"
        }

        # The binary, verified before it goes anywhere.
        Write-Host "Downloading choregos $($manifest.version) (windows/x86_64) from $InstanceUrl"
        $downloaded = Join-Path $work 'choregos.exe'
        Invoke-WebRequest -UseBasicParsing -Uri "$InstanceUrl/downloads/cli/$name" -OutFile $downloaded
        $actual = (Get-FileHash -Algorithm SHA256 -Path $downloaded).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            throw "choregos install: checksum mismatch for ${name}: expected $expected, got $actual; nothing was installed"
        }

        # A running choregos.exe cannot be overwritten, but it can be renamed: the old one steps
        # aside, the new one takes its name, and the old one is removed when nothing holds it.
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        $target = Join-Path $InstallDir 'choregos.exe'
        $aside = "$target.old"
        if (Test-Path -LiteralPath $aside) { Remove-Item -LiteralPath $aside -Force -ErrorAction SilentlyContinue }
        if (Test-Path -LiteralPath $target) { Move-Item -LiteralPath $target -Destination $aside -Force }
        try {
            Move-Item -LiteralPath $downloaded -Destination $target -Force
        } catch {
            if (Test-Path -LiteralPath $aside) { Move-Item -LiteralPath $aside -Destination $target -Force }
            throw
        }
        if (Test-Path -LiteralPath $aside) { Remove-Item -LiteralPath $aside -Force -ErrorAction SilentlyContinue }
        Write-Host "Installed choregos $($manifest.version) at $target"

        # On the user's PATH, for the next terminals, and for this one.
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not $userPath) { $userPath = '' }
        $entries = $userPath.Split(';') | Where-Object { $_ -ne '' }
        if ($entries -notcontains $InstallDir) {
            [Environment]::SetEnvironmentVariable('Path', (($entries + $InstallDir) -join ';'), 'User')
            Write-Host "Added $InstallDir to your user PATH (new terminals will see it)."
        }
        if (($env:Path.Split(';')) -notcontains $InstallDir) { $env:Path = "$env:Path;$InstallDir" }

        Write-Host ''
        Write-Host 'Next: create a full-access token in the console (My account, Access tokens), then'
        Write-Host "  choregos login --api-url $InstanceUrl --token <token> --org <organisation>"
        Write-Host 'Keep it in step with the instance later with: choregos self update'
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Install-Choregos
