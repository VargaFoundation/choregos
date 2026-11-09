import { deflateRawSync } from "node:zlib";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import OfficeView from "@/components/office-view";
import { viewerOf } from "@/lib/artifacts";
import { readOffice } from "@/lib/office";
import type { Presentation } from "@/lib/office/pptx";
import type { WordDocument, WordParagraph } from "@/lib/office/docx";
import type { Workbook } from "@/lib/office/xlsx";
import { openZip } from "@/lib/office/zip";

const fixture = (name: string): ArrayBuffer => {
  const data = readFileSync(join(__dirname, "fixtures/office", name));
  return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
};

beforeAll(() => {
  URL.createObjectURL = vi.fn(() => "blob:picture");
  URL.revokeObjectURL = vi.fn();
});

/** A ZIP of the given parts, deflated, with the sizes the central directory should declare. */
function zipOf(parts: Record<string, string | Uint8Array>): ArrayBuffer {
  const encoder = new TextEncoder();
  const locals: Uint8Array[] = [];
  const directory: Uint8Array[] = [];
  let offset = 0;
  for (const [name, content] of Object.entries(parts)) {
    const nameBytes = encoder.encode(name);
    const raw = typeof content === "string" ? encoder.encode(content) : content;
    const data = new Uint8Array(deflateRawSync(raw));
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(8, 8, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, raw.length, true);
    local.setUint16(26, nameBytes.length, true);
    locals.push(new Uint8Array(local.buffer), nameBytes, data);
    const central = new DataView(new ArrayBuffer(46));
    central.setUint32(0, 0x02014b50, true);
    central.setUint16(10, 8, true);
    central.setUint32(20, data.length, true);
    central.setUint32(24, raw.length, true);
    central.setUint16(28, nameBytes.length, true);
    central.setUint32(42, offset, true);
    directory.push(new Uint8Array(central.buffer), nameBytes);
    offset += 30 + nameBytes.length + data.length;
  }
  const size = directory.reduce((n, part) => n + part.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(10, Object.keys(parts).length, true);
  end.setUint32(12, size, true);
  end.setUint32(16, offset, true);
  const all = [...locals, ...directory, new Uint8Array(end.buffer)];
  const out = new Uint8Array(all.reduce((n, part) => n + part.length, 0));
  let at = 0;
  for (const part of all) {
    out.set(part, at);
    at += part.length;
  }
  return out.buffer;
}

const textOf = (paragraph: WordParagraph) => paragraph.runs.map((run) => run.text ?? "").join("");

describe("a presentation (ADR 0129)", () => {
  it("is read slide by slide, in order, with what placeholders inherit", async () => {
    const deck = (await readOffice("pptx", fixture("deck.pptx"))) as Presentation;
    expect(deck.slides).toHaveLength(3);
    expect(deck.width).toBe(9_144_000);
    const texts = deck.slides.map((slide) => slide.shapes.flatMap((s) => s.paragraphs.flatMap((p) => p.runs.map((r) => r.text))));
    expect(texts[0]).toEqual(["Quarterly business review", "Prepared for the directors"]);
    const title = deck.slides[1]!.shapes.find((s) => s.paragraphs[0]?.runs[0]?.text === "What changed")!;
    expect(title.width, "the position comes from the layout").toBeGreaterThan(0);
    expect(title.paragraphs[0]!.runs[0]!.sizePt, "the size from the master's title style").toBe(44);
    expect(deck.slides[1]!.shapes.find((s) => s.paragraphs[0]?.runs[0]?.text === "Revenue is up")!.paragraphs[0]!.bullet).toBe("•");
  });

  it("keeps a shape's fill, geometry and run formatting", async () => {
    const deck = (await readOffice("pptx", fixture("deck.pptx"))) as Presentation;
    const oval = deck.slides[1]!.shapes.find((s) => s.corners === "ellipse")!;
    expect(oval.fill).toBe("#E5007D");
    expect(oval.paragraphs[0]!.runs[0]).toMatchObject({ text: "Brand", sizePt: 20, bold: true });
  });

  it("draws pictures and tables", async () => {
    const deck = (await readOffice("pptx", fixture("deck.pptx"))) as Presentation;
    expect(deck.slides[2]!.shapes.find((s) => s.image)!.image).toBe("blob:picture");
    const table = deck.slides[2]!.shapes.find((s) => s.table)!.table!;
    expect(table[1]![1]![0]!.runs[0]!.text).toBe("120");
  });

  it("is drawn as slides, positioned against the slide", async () => {
    render(<OfficeView type="pptx" blob={new Blob([fixture("deck.pptx")])} fallback={null} />);
    expect(await screen.findAllByTestId("office-slide")).toHaveLength(3);
    expect(screen.getByTestId("office-pptx")).toHaveTextContent("Quarterly business review");
    expect(screen.getByTestId("office-note")).toHaveTextContent("preview");
  });
});

describe("a Word document (ADR 0129)", () => {
  it("is read with its headings, formatting, lists and alignment", async () => {
    const doc = (await readOffice("docx", fixture("memo.docx"))) as WordDocument;
    const paragraphs = doc.blocks.filter((b): b is WordParagraph => b.type === "paragraph");
    expect(paragraphs[0]).toMatchObject({ heading: 1 });
    expect(textOf(paragraphs[0]!)).toBe("Board memo");
    expect(paragraphs[1]!.runs[1]).toMatchObject({ text: "bold pink", bold: true, color: "#E5007D" });
    expect(paragraphs[2]!.align).toBe("center");
    expect(paragraphs[3]!.marker).toBe("•");
    expect([paragraphs[4]!.marker, paragraphs[5]!.marker]).toEqual(["1.", "2."]);
  });

  it("is drawn in the page, a picture and a table included", async () => {
    render(<OfficeView type="docx" blob={new Blob([fixture("memo.docx")])} fallback={null} />);
    const doc = await screen.findByTestId("office-docx");
    expect(within(doc).getByRole("heading", { level: 1, name: "Board memo" })).toBeInTheDocument();
    expect(doc.querySelector("img")).toHaveAttribute("src", "blob:picture");
    expect(within(doc).getByRole("table")).toHaveTextContent("LeftRight");
  });
});

describe("a workbook (ADR 0129)", () => {
  it("is read sheet by sheet, cells in their columns", async () => {
    const book = (await readOffice("xlsx", fixture("book.xlsx"))) as Workbook;
    expect(book.sheets.map((s) => s.name)).toEqual(["Sales", "Notes"]);
    expect(book.sheets[0]!.rows[0]).toEqual(["Region", "Amount", "Paid"]);
    expect(book.sheets[0]!.rows[1]).toEqual(["EMEA", "120.5", "TRUE"]);
    expect(book.sheets[0]!.rows[2]![4]).toBe("far");
  });

  it("switches sheets", async () => {
    render(<OfficeView type="xlsx" blob={new Blob([fixture("book.xlsx")])} fallback={null} />);
    expect(await screen.findByRole("table", { name: "sheet Sales" })).toHaveTextContent("EMEA");
    screen.getByRole("tab", { name: "Notes" }).click();
    expect(await screen.findByRole("table", { name: "sheet Notes" })).toBeInTheDocument();
  });
});

const RELS = (target: string, mode = "") =>
  `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="${target}" ${mode}/></Relationships>`;
const WITH_PICTURE = `<w:document xmlns:w="w" xmlns:a="a" xmlns:r="r"><w:body><w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p></w:body></w:document>`;

describe("a file an agent wrote is data (ADR 0129)", () => {
  it("never follows an external link", async () => {
    const created = vi.mocked(URL.createObjectURL);
    created.mockClear();
    const doc = (await readOffice(
      "docx",
      zipOf({ "word/document.xml": WITH_PICTURE, "word/_rels/document.xml.rels": RELS("https://evil.example/x.png", 'TargetMode="External"') }),
    )) as WordDocument;
    expect((doc.blocks[0] as WordParagraph).runs).toEqual([]);
    expect(created).not.toHaveBeenCalled();
  });

  it("draws no picture that is not raster bytes, whatever it is called", async () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>';
    const doc = (await readOffice(
      "docx",
      zipOf({ "word/document.xml": WITH_PICTURE, "word/_rels/document.xml.rels": RELS("media/x.png"), "word/media/x.png": svg }),
    )) as WordDocument;
    expect((doc.blocks[0] as WordParagraph).runs).toEqual([]);
  });

  it("draws text as text", async () => {
    const xml = `<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>&lt;img src=x onerror=alert(1)&gt;</w:t></w:r></w:p></w:body></w:document>`;
    const buffer = zipOf({ "word/document.xml": xml });
    const { container } = render(<OfficeView type="docx" blob={new Blob([buffer])} fallback={null} />);
    const doc = await screen.findByTestId("office-docx");
    expect(doc).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
  });

  it("stops inflating an entry past its bound, whatever the archive declares", async () => {
    const bomb = zipOf({ huge: new Uint8Array(65 * 1024 * 1024) });
    await expect(openZip(bomb).bytes("huge")).rejects.toThrow("too large");
  });

  it("refuses what is not its type and offers the download", async () => {
    render(<OfficeView type="pptx" blob={new Blob([fixture("memo.docx")])} fallback={<span>download it</span>} />);
    expect(await screen.findByTestId("office-refused")).toHaveTextContent("not a pptx file");
    expect(screen.getByText("download it")).toBeInTheDocument();
  });

  it("refuses bytes that are no archive", async () => {
    render(<OfficeView type="docx" blob={new Blob([new Uint8Array(2048)])} fallback={<span>download it</span>} />);
    expect(await screen.findByTestId("office-refused")).toHaveTextContent("not an Office file");
  });

  it("is what an office artifact opens in", () => {
    for (const type of ["docx", "pptx", "xlsx"] as const) {
      expect(viewerOf({ type, kind: "file", media_type: "application/vnd.openxmlformats-officedocument" })).toBe("office");
    }
  });
});
