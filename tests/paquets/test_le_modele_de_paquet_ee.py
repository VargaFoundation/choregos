# SPDX-License-Identifier: Apache-2.0
"""Does a **real** enterprise-edition wheel carry an entry point the platform finds?

This is the milestone E4 manufacturing question, and it is distinct from the other plugin
tests. `test_greffons.py` and `test_greffon_entreprise.py` write a `.dist-info` **by hand**:
they prove that discovery works, not that the wheel one would publish carries it. What breaks
silently is the metadata — a mistake in `[project.entry-points."choregos.plugins"]` produces a
wheel that installs perfectly and never registers.

TWO MANUFACTURING CONSTRAINTS, checked here because they decide the shape of the private repo:

1. **The core is not a declared dependency.** Its wheels are GitHub release artifacts, not
   PyPI ones (`release.yml`: no PyPI until the names are reserved). An EE wheel that declared
   `choregos-api==0.8.3` would send pip looking in an index that does not have it. The plan
   calls for EE images built **FROM** the CE images, where the core packages are already
   installed: the EE wheel is added with `--no-deps`, so this test builds a wheel without
   dependencies.
2. **The entry point is enough.** No import of the core when the module loads: `brancher()`
   imports on call. An EE wheel imported too early would break on a missing core, in a build
   bench for example.

Checked by hand on 2026-09-28 in an environment where the core is complete: the wheel installs
with `--no-deps`, `charger_les_greffons()` loads it, the edition becomes `enterprise`, and
uninstalling returns `community`. This test guards the half that can be checked without
polluting the suite's environment; the other half — the wheel placed in a real CE image — is
`test_image_ee.py`. The wheel itself is built in `conftest.py`.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv missing"),
]


def test_la_roue_porte_son_point_d_entree(roue_ee: pathlib.Path) -> None:
    """The published wheel's metadata, not a hand-written `.dist-info`.

    This is what breaks silently: a mistake in `[project.entry-points."choregos.plugins"]`
    gives a wheel that installs perfectly and never registers.
    """
    with zipfile.ZipFile(roue_ee) as archive:
        noms = archive.namelist()
        chemin = next((n for n in noms if n.endswith("entry_points.txt")), None)
        assert chemin is not None, f"no entry_points.txt in the wheel: {noms}"
        contenu = archive.read(chemin).decode("utf-8")

    assert "[choregos.plugins]" in contenu, contenu
    assert "choregos-ee = choregos_ee:brancher" in contenu, contenu


def test_la_roue_ne_depend_pas_du_coeur(roue_ee: pathlib.Path) -> None:
    """Declaring the core would send pip looking in an index that does not have it: its wheels
    are GitHub release artifacts, not PyPI ones."""
    with zipfile.ZipFile(roue_ee) as archive:
        chemin = next(n for n in archive.namelist() if n.endswith("METADATA"))
        metadata = archive.read(chemin).decode("utf-8")

    requises = [ligne for ligne in metadata.splitlines() if ligne.startswith("Requires-Dist:")]
    assert not requises, f"an EE wheel does not declare the core as a dependency: {requises}"


def test_le_module_ne_touche_pas_au_coeur_a_l_import(roue_ee: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """Importable without the core: a build bench does not have Choregos installed.

    We unpack the wheel into an isolated directory and import it in an interpreter **without**
    this workspace's `sys.path` — the only way to prove that no core import fires when the
    module loads.
    """
    deplie = tmp_path / "deplie"
    with zipfile.ZipFile(roue_ee) as archive:
        archive.extractall(deplie)

    fait = subprocess.run(
        [sys.executable, "-S", "-c", "import choregos_ee; print(choregos_ee.brancher.__name__)"],
        cwd=deplie,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(deplie), "PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert fait.returncode == 0, f"the module does not import without the core:\n{fait.stderr[-600:]}"
    assert fait.stdout.strip() == "brancher"
