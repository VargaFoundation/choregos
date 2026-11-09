// SPDX-License-Identifier: Apache-2.0
/** A Word document as blocks of text, tables and pictures, in reading order. */
import { attribute, child, children, descendants, isOn, numberOf, part, Pictures, relationshipsOf } from "./xml";
import type { Zip } from "./zip";

const MAX_BLOCKS = 5_000;
const EMU_PER_PIXEL = 9525;

export type WordRun = {
  text?: string;
  image?: { url: string; width: number; height: number };
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  color?: string;
  sizePt?: number;
};
export type WordParagraph = {
  type: "paragraph";
  runs: WordRun[];
  heading: number | null;
  align?: "left" | "center" | "right" | "justify";
  marker?: string;
  level: number;
};
export type WordTable = { type: "table"; rows: WordParagraph[][][] };
export type WordDocument = { kind: "docx"; blocks: (WordParagraph | WordTable)[]; urls: string[]; truncated: boolean };

type Numbering = { format(id: string, level: number): string };

async function readNumbering(zip: Zip): Promise<Numbering> {
  const root = await part(zip, "word/numbering.xml");
  const abstracts = new Map(children(root, "abstractNum").map((item) => [attribute(item, "abstractNumId") ?? "", item]));
  const instances = new Map(children(root, "num").map((item) => [attribute(item, "numId") ?? "", attribute(child(item, "abstractNumId"), "val") ?? ""]));
  return {
    format(id, level) {
      const levels = children(abstracts.get(instances.get(id) ?? ""), "lvl");
      const found = levels.find((item) => attribute(item, "ilvl") === String(level));
      return attribute(child(found, "numFmt"), "val") ?? "bullet";
    },
  };
}

type Styles = { headings: Map<string, number>; lists: Map<string, { id: string; level: number }> };

async function readStyles(zip: Zip): Promise<Styles> {
  const root = await part(zip, "word/styles.xml");
  const styles: Styles = { headings: new Map(), lists: new Map() };
  for (const style of children(root, "style")) {
    const id = attribute(style, "styleId") ?? "";
    const name = (attribute(child(style, "name"), "val") ?? "").toLowerCase();
    const heading = /^heading (\d)$/.exec(name);
    if (heading) styles.headings.set(id, Number(heading[1]));
    else if (name === "title") styles.headings.set(id, 1);
    // A list style carries its numbering, and the paragraph then names only the style.
    const numbered = child(style, "pPr", "numPr");
    const numId = attribute(child(numbered, "numId"), "val");
    if (numId) styles.lists.set(id, { id: numId, level: numberOf(child(numbered, "ilvl"), "val") ?? 0 });
  }
  return styles;
}

const ALIGN: Record<string, WordParagraph["align"]> = { left: "left", start: "left", center: "center", right: "right", end: "right", both: "justify" };
const HEX = /^[0-9A-Fa-f]{6}$/;

class Reader {
  private readonly counters = new Map<string, number[]>();

  constructor(
    private readonly relations: Awaited<ReturnType<typeof relationshipsOf>>,
    private readonly pictures: Pictures,
    private readonly numbering: Numbering,
    private readonly styles: Styles,
  ) {}

  private marker(id: string, level: number): string {
    const format = this.numbering.format(id, level);
    if (format === "bullet" || format === "none") return format === "none" ? "" : "•";
    const counts = this.counters.get(id) ?? [];
    counts[level] = (counts[level] ?? 0) + 1;
    counts.length = level + 1;
    this.counters.set(id, counts);
    return `${counts[level]}.`;
  }

  private async run(element: Element): Promise<WordRun[]> {
    const props = child(element, "rPr");
    const on = (name: string) => {
      const flag = child(props, name);
      return flag !== null && (attribute(flag, "val") === null || isOn(attribute(flag, "val")));
    };
    const color = attribute(child(props, "color"), "val") ?? "";
    const half = numberOf(child(props, "sz"), "val");
    const style: WordRun = {
      bold: on("b"),
      italic: on("i"),
      underline: isOn(attribute(child(props, "u"), "val")),
      color: HEX.test(color) ? `#${color}` : undefined,
      sizePt: half === null ? undefined : half / 2,
    };
    const runs: WordRun[] = [];
    for (const item of children(element)) {
      if (item.localName === "t") runs.push({ ...style, text: item.textContent ?? "" });
      else if (item.localName === "tab") runs.push({ ...style, text: "\t" });
      else if (item.localName === "br") runs.push({ ...style, text: "\n" });
      else if (item.localName === "drawing") {
        const blip = descendants(item, "blip")[0];
        const relation = this.relations.get(attribute(blip, "embed") ?? "");
        const url = relation && !relation.external ? await this.pictures.url(relation.target) : null;
        const extent = descendants(item, "extent")[0];
        if (url) {
          runs.push({
            image: {
              url,
              width: (numberOf(extent, "cx") ?? 0) / EMU_PER_PIXEL,
              height: (numberOf(extent, "cy") ?? 0) / EMU_PER_PIXEL,
            },
          });
        }
      }
    }
    return runs;
  }

  async paragraph(element: Element): Promise<WordParagraph> {
    const props = child(element, "pPr");
    const styleName = attribute(child(props, "pStyle"), "val") ?? "";
    const numbered = child(props, "numPr");
    const fromStyle = this.styles.lists.get(styleName);
    const level = numberOf(child(numbered, "ilvl"), "val") ?? fromStyle?.level ?? 0;
    const id = attribute(child(numbered, "numId"), "val") ?? fromStyle?.id;
    const runs: WordRun[] = [];
    for (const item of children(element)) {
      if (item.localName === "r") runs.push(...(await this.run(item)));
      else if (item.localName === "hyperlink") for (const run of children(item, "r")) runs.push(...(await this.run(run)));
    }
    return {
      type: "paragraph",
      runs,
      heading: this.styles.headings.get(styleName) ?? null,
      align: ALIGN[attribute(child(props, "jc"), "val") ?? ""],
      marker: id && id !== "0" ? this.marker(id, level) : undefined,
      level,
    };
  }

  async table(element: Element): Promise<WordTable> {
    const rows: WordParagraph[][][] = [];
    for (const row of children(element, "tr")) {
      const cells: WordParagraph[][] = [];
      for (const cell of children(row, "tc")) {
        const paragraphs: WordParagraph[] = [];
        for (const item of children(cell, "p")) paragraphs.push(await this.paragraph(item));
        cells.push(paragraphs);
      }
      rows.push(cells);
    }
    return { type: "table", rows };
  }
}

export async function readDocument(zip: Zip): Promise<WordDocument> {
  const path = "word/document.xml";
  const [root, relations, numbering, styles] = await Promise.all([
    part(zip, path),
    relationshipsOf(zip, path),
    readNumbering(zip),
    readStyles(zip),
  ]);
  const pictures = new Pictures(zip);
  const reader = new Reader(relations, pictures, numbering, styles);
  const body = children(child(root, "body")).filter((item) => item.localName === "p" || item.localName === "tbl");
  const blocks: WordDocument["blocks"] = [];
  for (const item of body.slice(0, MAX_BLOCKS)) {
    blocks.push(item.localName === "p" ? await reader.paragraph(item) : await reader.table(item));
  }
  return { kind: "docx", blocks, urls: pictures.urls, truncated: body.length > MAX_BLOCKS };
}
