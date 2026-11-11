#!/usr/bin/env bash
# The Linux build of the native CLI, inside the manylinux_2_28 image (`build.py --manylinux`, S62-07).
# Runs as root in a throwaway container: installs ccache (EPEL) and uv, runs build.py with the image's
# Python 3.12, then gives the files it wrote back to the user who started it.
set -euo pipefail
give_back() {
  chown -R "${HOST_UID}:${HOST_GID}" build/cli-binary "${OUT}" 2>/dev/null || true
}
trap give_back EXIT
# ccache: a second build reuses every unchanged module's object file. Optional: without it the build
# is only slower, so a mirror that fails does not fail the build.
if ! command -v ccache >/dev/null; then
  (dnf install -y -q epel-release && dnf install -y -q ccache) >/dev/null 2>&1 \
    || echo "ccache not installed: the build will not reuse compiled modules"
fi
# The image's Pythons are linked statically and ship their libpython only in this archive, "for
# embedding": Nuitka links it into the binary, so no libpython goes into the payload.
if [ -f /opt/_internal/static-libs-for-embedding-only.tar.xz ]; then
  tar -C /opt/_internal -xf /opt/_internal/static-libs-for-embedding-only.tar.xz
fi
"${PYTHON}" -m pip install --quiet --disable-pip-version-check --root-user-action=ignore "uv==0.13.7"
export PATH="$(dirname "${PYTHON}"):${PATH}"
"${PYTHON}" tools/cli_binary/build.py --python "${PYTHON}" --out "${OUT}"
