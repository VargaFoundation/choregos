# SPDX-License-Identifier: Apache-2.0
"""A document's outline: numbered by nesting, fence-aware, each section holding its subsections."""

from __future__ import annotations

from tranibor_core.outline import find, outline, render

DOCUMENT = """# Security policy

Scope of the policy.

## Encryption

### At rest

AES-256 everywhere.

### In transit

TLS 1.3 everywhere.

```yaml
# not a heading
ciphers: [tls13]
```

## Access

#### Reviews

Quarterly.

# Annexes

The tables.
"""


def test_sections_are_numbered_by_nesting_and_hold_their_subsections() -> None:
    sections = outline(DOCUMENT)
    assert [(s.number, s.title) for s in sections] == [
        ("1", "Security policy"),
        ("1.1", "Encryption"),
        ("1.1.1", "At rest"),
        ("1.1.2", "In transit"),
        ("1.2", "Access"),
        ("1.2.1", "Reviews"),  # a `####` under a `##` is the next depth, not 1.2.0.1
        ("2", "Annexes"),
    ]
    encryption = sections[1]
    body = DOCUMENT[encryption.start : encryption.end]
    assert body.startswith("## Encryption") and "TLS 1.3" in body and "## Access" not in body
    assert sections[0].end == sections[-1].start  # the first `#` runs up to the next `#`
    assert sections[-1].end == len(DOCUMENT)
    assert sections[3].path == ("Security policy", "Encryption", "In transit")
    assert DOCUMENT.splitlines()[sections[3].line - 1] == "### In transit"


def test_a_hash_inside_a_fence_is_not_a_heading() -> None:
    assert "not a heading" not in {s.title for s in outline(DOCUMENT)}
    assert outline("```\n# code\n```\n") == []


def test_a_document_without_headings_has_no_outline() -> None:
    assert outline("Just a paragraph.\n\nAnother one.\n") == []
    assert render([]) == ""


def test_a_section_is_found_by_number_path_or_unique_title() -> None:
    sections = outline(DOCUMENT)
    assert find(sections, "1.1.2") is sections[3]
    assert find(sections, "§ 1.1.2.") is sections[3]
    assert find(sections, "Security policy > Encryption > In transit") is sections[3]
    # A search hit's path starts with the file name: the end of the path still names the section.
    assert find(sections, "policy.md > Security policy > Encryption > In transit") is sections[3]
    assert find(sections, "in   TRANSIT") is sections[3]
    assert find(sections, "Encryption > Nowhere") is None
    assert find(sections, "") is None


def test_an_ambiguous_title_is_not_guessed() -> None:
    sections = outline("# A\n\n## Examples\n\nx\n\n# B\n\n## Examples\n\ny\n")
    assert find(sections, "Examples") is None
    assert find(sections, "B > Examples") is sections[3]
    assert find(sections, "2.1") is sections[3]


def test_render_lists_sizes_and_lines_and_counts_what_it_leaves_out() -> None:
    sections = outline(DOCUMENT)
    full = render(sections, lines=True)
    assert "  - 1.1 Encryption — " in full and "(line 5)" in full
    shallow = render(sections, max_depth=2)
    assert "At rest" not in shallow and "… 3 more section(s) not listed" in shallow
    assert "(line" not in shallow
    assert render(sections, max_lines=2).endswith("… 5 more section(s) not listed")


def test_a_yaml_header_is_not_text_and_the_lines_stay_the_documents() -> None:
    """An OKF header (ADR 0174) opens every served document; a `# comment` in it is no heading."""
    served = "---\ntype: Document\n# a YAML comment\ntitle: Policy\n---\n# Policy\n\n## Scope\n\nAll.\n"
    sections = outline(served)
    assert [(s.number, s.title, s.line) for s in sections] == [("1", "Policy", 6), ("1.1", "Scope", 8)]
    assert served[sections[1].start :].startswith("## Scope")
    # A rule (`---`) further down is not a header.
    assert [s.title for s in outline("# A\n\n---\n# B\n\n---\n")] == ["A", "B"]
