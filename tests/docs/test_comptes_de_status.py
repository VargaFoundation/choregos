"""Le total annoncé par STATUS est-il celui de son propre tableau ?

`docs/plan/STATUS.md` affirmait « Total : 96 livrées, 6 partielles » sous un tableau qui en
comptait 92 et 10. Le chiffre était recopié à la main et avait cessé de suivre. C'est le genre
d'écart qui ne blesse personne le jour où il naît, et qui rend le document inutilisable le jour
où quelqu'un s'en sert pour décider.

Le total est maintenant vérifié, pas relu.
"""

from __future__ import annotations

import pathlib
import re

STATUS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "plan" / "STATUS.md"
TOTAL = re.compile(r"\*\*Total\*\* : (\d+) livrées, (\d+) partielles, (\d+) non commencée", re.M)


def test_le_total_est_celui_du_tableau() -> None:
    texte = STATUS.read_text(encoding="utf-8")
    annonce = TOTAL.search(texte)
    assert annonce, "STATUS n'annonce plus de total — ou sa forme a changé sans ce test"
    compte = (texte.count("| ✅ |"), texte.count("| 🟡 |"), texte.count("| ⬜ |"))
    assert compte[0] > 0, "aucune story livrée comptée : le tableau a changé de forme"
    assert tuple(int(n) for n in annonce.groups()) == compte, (
        f"STATUS annonce {annonce.groups()} et son tableau dit {compte}"
    )


STORY_ROW = re.compile(r"^\| ([A-Z][A-Za-z0-9-]+) \| ", re.M)


def test_each_story_has_one_row() -> None:
    """A rebase that keeps both sides leaves a story twice, and the total counts it twice (#794)."""
    ids = STORY_ROW.findall(STATUS.read_text(encoding="utf-8"))
    assert len(ids) > 100, "the table no longer parses: its row shape changed without this test"
    twice = sorted({story for story in ids if ids.count(story) > 1} - {"Story"})
    assert not twice, f"STATUS has these stories on more than one row: {twice}"
