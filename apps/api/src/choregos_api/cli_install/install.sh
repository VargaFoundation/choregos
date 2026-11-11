#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# Installs the `choregos` CLI from a Choregos instance, in the version that instance runs.
#
#   curl -fsSL __CHOREGOS_INSTANCE_URL__/cli/install.sh | sh
#   curl -fsSL __CHOREGOS_INSTANCE_URL__/cli/install.sh | sh -s -- --prefix "$HOME/tools"
#
# Linux and macOS, x86_64 and arm64. It downloads the binary from the same instance, checks its
# sha256 against the instance's manifest, and installs it as `<prefix>/bin/choregos`
# (`~/.local/bin/choregos` by default). It never calls sudo: pick a prefix you can write to.
#
# Options:
#   --prefix DIR   install into DIR/bin (default: $HOME/.local; also CHOREGOS_INSTALL_PREFIX)
#   --url URL      the instance to install from (default: the one that served this script)
#   --help         this text
set -eu

INSTANCE_URL='__CHOREGOS_INSTANCE_URL__'
PREFIX="${CHOREGOS_INSTALL_PREFIX:-${HOME:-}/.local}"
RELEASES='https://github.com/VargaFoundation/choregos/releases'

say() { printf '%s\n' "$*"; }
fail() { printf 'choregos install: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Installs the choregos CLI from $INSTANCE_URL, in the version it runs.

Usage: sh install.sh [--prefix DIR] [--url URL]
  --prefix DIR   install into DIR/bin (default: \$HOME/.local; also CHOREGOS_INSTALL_PREFIX)
  --url URL      the instance to install from (default: $INSTANCE_URL)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) [ $# -ge 2 ] || fail "--prefix needs a directory"; PREFIX="$2"; shift 2 ;;
    --prefix=*) PREFIX="${1#--prefix=}"; shift ;;
    --url) [ $# -ge 2 ] || fail "--url needs a URL"; INSTANCE_URL="$2"; shift 2 ;;
    --url=*) INSTANCE_URL="${1#--url=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1 (try --help)" ;;
  esac
done
INSTANCE_URL="${INSTANCE_URL%/}"
[ -n "$PREFIX" ] || fail "no prefix: HOME is not set, pass --prefix DIR"

# ── what this machine is ──
case "$(uname -s)" in
  Linux) OS=linux ;;
  Darwin) OS=macos ;;
  *) fail "unsupported system $(uname -s): on Windows, run: irm $INSTANCE_URL/cli/install.ps1 | iex" ;;
esac
case "$(uname -m)" in
  x86_64|amd64) ARCH=x86_64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) fail "unsupported processor $(uname -m): the CLI is built for x86_64 and arm64" ;;
esac
# A shell under Rosetta says x86_64 on an Apple processor: the native binary is the better one.
if [ "$OS" = macos ] && [ "$ARCH" = x86_64 ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
  ARCH=arm64
fi
NAME="choregos-$OS-$ARCH"

# ── the tools it needs ──
if command -v curl >/dev/null 2>&1; then
  fetch() { curl -fsSL --retry 2 -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -q -O "$2" "$1"; }
else
  fail "neither curl nor wget is installed"
fi
if command -v sha256sum >/dev/null 2>&1; then
  sha256_of() { sha256sum "$1" | cut -d ' ' -f 1; }
elif command -v shasum >/dev/null 2>&1; then
  sha256_of() { shasum -a 256 "$1" | cut -d ' ' -f 1; }
elif command -v openssl >/dev/null 2>&1; then
  sha256_of() { openssl dgst -sha256 "$1" | sed 's/^.*= *//'; }
else
  fail "no sha256 tool (sha256sum, shasum or openssl) to verify the download"
fi

WORK="$(mktemp -d 2>/dev/null || mktemp -d -t choregos)"
trap 'rm -rf "$WORK"' EXIT
trap 'exit 130' INT TERM

# ── the manifest: the version, and the checksum of this machine's binary ──
fetch "$INSTANCE_URL/downloads/cli/manifest.json" "$WORK/manifest.json" ||
  fail "cannot read $INSTANCE_URL/downloads/cli/manifest.json: this instance may bundle no CLI; see $RELEASES"
# No jq on most machines: the manifest is flattened, one JSON object per line, and read with sed.
# File names and checksums hold no space, so removing every blank changes nothing that is read.
FLAT="$(tr -d ' \t\r\n' < "$WORK/manifest.json" | tr '{' '\n' | tr '}' '\n')"
VERSION="$(printf '%s\n' "$FLAT" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p' | head -n 1)"
ENTRY="$(printf '%s\n' "$FLAT" | grep "\"name\":\"$NAME\"" | head -n 1 || true)"
[ -n "$ENTRY" ] || fail "this instance's CLI ${VERSION:-?} has no binary for $OS/$ARCH ($NAME); see $RELEASES"
EXPECTED="$(printf '%s\n' "$ENTRY" | sed -n 's/.*"sha256":"\([0-9a-f]\{64\}\)".*/\1/p')"
[ -n "$EXPECTED" ] || fail "the manifest gives no sha256 for $NAME"

# ── the binary, verified before it goes anywhere ──
say "Downloading choregos ${VERSION:-?} ($OS/$ARCH) from $INSTANCE_URL"
fetch "$INSTANCE_URL/downloads/cli/$NAME" "$WORK/choregos" || fail "cannot download $INSTANCE_URL/downloads/cli/$NAME"
ACTUAL="$(sha256_of "$WORK/choregos")"
[ "$ACTUAL" = "$EXPECTED" ] || fail "checksum mismatch for $NAME: expected $EXPECTED, got $ACTUAL; nothing was installed"

# ── installed by a rename in the target directory: a running choregos is never half-written ──
BIN_DIR="$PREFIX/bin"
mkdir -p "$BIN_DIR" 2>/dev/null || fail "cannot create $BIN_DIR: pass --prefix with a directory you own"
[ -w "$BIN_DIR" ] || fail "$BIN_DIR is not writable: pass --prefix with a directory you own (this script never uses sudo)"
STAGED="$BIN_DIR/.choregos.$$.tmp"
cp "$WORK/choregos" "$STAGED"
chmod 0755 "$STAGED"
mv -f "$STAGED" "$BIN_DIR/choregos"
say "Installed choregos ${VERSION:-?} at $BIN_DIR/choregos"

case ":${PATH:-}:" in
  *":$BIN_DIR:"*) ;;
  *)
    say ""
    say "$BIN_DIR is not on your PATH. Add it, then open a new terminal:"
    case "${SHELL:-}" in
      */zsh) say "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc" ;;
      */fish) say "  fish_add_path $BIN_DIR" ;;
      *) say "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.profile" ;;
    esac
    ;;
esac

say ""
say "Next: create a full-access token in the console (My account, Access tokens), then"
say "  choregos login --api-url $INSTANCE_URL --token <token> --org <organisation>"
say "Keep it in step with the instance later with: choregos self update"
