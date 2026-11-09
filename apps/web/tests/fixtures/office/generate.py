# SPDX-License-Identifier: Apache-2.0
"""Writes the Office files of `office-view.test.tsx`, with the libraries an agent's skill would use.

    uv run python apps/web/tests/fixtures/office/generate.py
"""

import base64
import datetime
import io
import pathlib

import openpyxl
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import RGBColor as DocxColor
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

HERE = pathlib.Path(__file__).parent
FIXED = datetime.datetime(2026, 1, 1)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def deck() -> None:
    presentation = Presentation()
    presentation.core_properties.created = FIXED
    presentation.core_properties.modified = FIXED
    title = presentation.slides.add_slide(presentation.slide_layouts[0])
    title.shapes.title.text = "Quarterly business review"
    title.placeholders[1].text = "Prepared for the directors"
    content = presentation.slides.add_slide(presentation.slide_layouts[1])
    content.shapes.title.text = "What changed"
    body = content.placeholders[1].text_frame
    body.text = "Revenue is up"
    body.add_paragraph().text = "Costs are flat"
    box = content.shapes.add_shape(MSO_SHAPE.OVAL, Inches(7), Inches(5), Inches(2), Inches(1))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0xE5, 0x00, 0x7D)
    box.text_frame.text = "Brand"
    box.text_frame.paragraphs[0].runs[0].font.size = Pt(20)
    box.text_frame.paragraphs[0].runs[0].font.bold = True
    last = presentation.slides.add_slide(presentation.slide_layouts[5])
    last.shapes.title.text = "Numbers"
    last.shapes.add_picture(io.BytesIO(PNG), Inches(1), Inches(2), Inches(1), Inches(1))
    table = last.shapes.add_table(2, 2, Inches(3), Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).text = "Region"
    table.cell(0, 1).text = "Sales"
    table.cell(1, 0).text = "EMEA"
    table.cell(1, 1).text = "120"
    presentation.save(HERE / "deck.pptx")


def word() -> None:
    document = Document()
    document.core_properties.created = FIXED
    document.core_properties.modified = FIXED
    document.add_heading("Board memo", level=1)
    paragraph = document.add_paragraph("Plain then ")
    run = paragraph.add_run("bold pink")
    run.bold = True
    run.font.color.rgb = DocxColor(0xE5, 0x00, 0x7D)
    centred = document.add_paragraph("Centred")
    centred.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph("First point", style="List Bullet")
    document.add_paragraph("Step one", style="List Number")
    document.add_paragraph("Step two", style="List Number")
    document.add_picture(io.BytesIO(PNG))
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Left"
    table.cell(0, 1).text = "Right"
    document.save(HERE / "memo.docx")


def workbook() -> None:
    book = openpyxl.Workbook()
    book.properties.created = FIXED
    book.properties.modified = FIXED
    sales = book.active
    sales.title = "Sales"
    sales.append(["Region", "Amount", "Paid"])
    sales.append(["EMEA", 120.5, True])
    sales["E3"] = "far"
    other = book.create_sheet("Notes")
    other.append(["=SUM(A1)"])
    book.save(HERE / "book.xlsx")


deck()
word()
workbook()
