"""Does the ADR index list ALL the decisions, with the right status?

`docs/adr/README.md` used to carry two tables. The first, kept by hand, stopped at 0014: it told
a reader that the nine following decisions did not exist, among them the ones that carry the
authorship attestation, the `agent-sandbox` executor and SPIFFE. Nobody saw it because nothing
compared the folder with its index.

Two properties: every file of the folder is in the index, and the index's status is the one the
decision itself declares. The second catches the sneakier case: an ADR moved from `proposed` to
`accepted` in its file, and left `proposed` in the index.
"""

from __future__ import annotations

import pathlib
import re

ADR = pathlib.Path(__file__).resolve().parents[2] / "docs" / "adr"
INDEX = ADR / "README.md"
#: `| [0015](0015-….md) | summary | proposed |`
LINE = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|[^|]*\|\s*([a-z ]+?)\s*\|", re.M)
#: `- **Status**: proposed, 2026-09-25 — …`
STATUS = re.compile(r"^-\s+\*\*Status\*\*:\s*([a-z]+)", re.M)


def _index() -> dict[str, tuple[str, str]]:
    """{number: (linked file, announced status)}."""
    return {num: (file, status) for num, file, status in LINE.findall(INDEX.read_text("utf-8"))}


def _files() -> dict[str, pathlib.Path]:
    # A renamed decision leaves a one-line redirect under its old name (S34-05): not a decision.
    return {
        path.name[:4]: path
        for path in sorted(ADR.glob("[0-9][0-9][0-9][0-9]-*.md"))
        if not path.read_text("utf-8").startswith("# Moved\n")
    }


def test_the_index_forgets_no_decision() -> None:
    files, index = _files(), _index()
    assert files, "no ADR found: the test checks nothing any more"
    missing = sorted(set(files) - set(index))
    assert not missing, f"decisions missing from the index: {missing}"
    phantoms = sorted(set(index) - set(files))
    assert not phantoms, f"the index cites decisions that do not exist: {phantoms}"


def test_the_link_of_the_index_points_at_the_right_file() -> None:
    files, index = _files(), _index()
    wrong = [num for num, (link, _) in index.items() if link != files[num].name]
    assert not wrong, f"links that do not point at the decision's file: {wrong}"


def test_the_status_of_the_index_is_the_one_of_the_decision() -> None:
    """A decision accepted in its file and "proposed" in the index is a readable lie."""
    gaps = []
    for num, (_, announced) in _index().items():
        text = _files()[num].read_text("utf-8")
        actual = STATUS.search(text)
        assert actual, f"decision {num} declares no status"
        if actual.group(1) != announced:
            gaps.append(f"{num}: index={announced!r}, file={actual.group(1)!r}")
    assert not gaps, "the index and the decisions do not say the same thing:\n  " + "\n  ".join(gaps)
