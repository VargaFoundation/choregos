"""Small documents built at test time: nothing binary is committed (ADR 0051 tests)."""

from __future__ import annotations

import io
import zipfile


def pptx(
    slides: list[tuple[str, str]],
    table: list[list[str]] | None = None,
    notes: dict[int, str] | None = None,
    chart: tuple[list[str], dict[str, list[float]]] | None = None,
) -> bytes:
    """A deck: one slide per `(title, body)` (with the speaker `notes` of slide n, 0-based), a slide holding
    `table` if given, and a last one holding a column `chart` (categories, series) if given."""
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    deck = Presentation()
    for index, (title, body) in enumerate(slides):
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
        if notes and index in notes:
            slide.notes_slide.notes_text_frame.text = notes[index]
    if table:
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        slide.shapes.title.text = "Table"
        grid = slide.shapes.add_table(
            len(table), len(table[0]), Inches(1), Inches(2), Inches(6), Inches(1)
        ).table
        for i, row in enumerate(table):
            for j, cell in enumerate(row):
                grid.cell(i, j).text = cell
    if chart:
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        slide.shapes.title.text = "Chart"
        data = CategoryChartData()
        data.categories = chart[0]
        for name, values in chart[1].items():
            data.add_series(name, values)
        slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(6), Inches(4), data
        )
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def docx(sections: list[tuple[int, str, str]], table: list[list[str]] | None = None) -> bytes:
    """A Word document: `(level, heading, paragraph)` per section, then `table` if given."""
    import docx as python_docx

    document = python_docx.Document()
    for level, heading, paragraph in sections:
        document.add_heading(heading, level)
        if paragraph:
            document.add_paragraph(paragraph)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for i, row in enumerate(table):
            for j, cell in enumerate(row):
                grid.cell(i, j).text = cell
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    first = True
    for name, rows in sheets.items():
        sheet = book.active if first else book.create_sheet()
        first = False
        assert sheet is not None
        sheet.title = name
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _pdf_string(text: str) -> str:
    return "(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _outline_objects(bookmarks: list[tuple[int, str]], first: int) -> list[bytes]:
    """The `/Outlines` dictionary (object `first`) and one item per bookmark (depth from 1, title),
    nested by depth, each pointing at page 3."""
    parents: list[int] = []  # for each item, the object number of its parent
    stack: list[tuple[int, int]] = []  # depth, object number
    for n, (depth, _) in enumerate(bookmarks):
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parents.append(stack[-1][1] if stack else first)
        stack.append((depth, first + 1 + n))
    numbers = [first + 1 + n for n in range(len(bookmarks))]
    kids = {
        parent: [k for k, p in zip(numbers, parents, strict=True) if p == parent]
        for parent in {first, *numbers}
    }

    def links(number: int) -> str:
        children = kids.get(number) or []
        return (
            f" /First {children[0]} 0 R /Last {children[-1]} 0 R /Count {len(children)}" if children else ""
        )

    objects = [f"<< /Type /Outlines{links(first)} >>".encode()]
    for number, parent, (_, title) in zip(numbers, parents, bookmarks, strict=True):
        siblings = kids[parent]
        at = siblings.index(number)
        around = (f" /Prev {siblings[at - 1]} 0 R" if at else "") + (
            f" /Next {siblings[at + 1]} 0 R" if at + 1 < len(siblings) else ""
        )
        item = f"/Title {_pdf_string(title)} /Parent {parent} 0 R{around}{links(number)} /Dest [3 0 R /Fit]"
        objects.append(f"<< {item} >>".encode())
    return objects


def pdf(lines: list[str], bookmarks: list[tuple[int, str]] | None = None) -> bytes:
    """A one-page PDF with `lines` of Helvetica text, written by hand (no PDF library needed); with
    `bookmarks` (depth from 1, title), its outline, as a word processor exports its headings."""
    text = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(_pdf_string(line) + " '" for line in lines) + " ET"
    outlines = " /Outlines 6 0 R /PageMode /UseOutlines" if bookmarks else ""
    objects = [
        f"<< /Type /Catalog /Pages 2 0 R{outlines} >>".encode(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text.encode() + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        *(_outline_objects(bookmarks, 6) if bookmarks else []),
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def broken_office() -> bytes:
    """A zip that claims to be an Office document and is not one: the converter fails on it."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "not xml at all <<<")
    return out.getvalue()
