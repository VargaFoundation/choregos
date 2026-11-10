// SPDX-License-Identifier: Apache-2.0
/**
 * A presentation as positioned boxes: what the slide, its layout and its master draw, with the theme's
 * colours and the inheritance of placeholders. A preview, not PowerPoint: charts and effects are not drawn.
 */
import { attribute, child, children, isOn, numberOf, part, Pictures, type Relationships, relationIdOf, relationshipsOf } from "./xml";
import type { Zip } from "./zip";

export const MAX_SLIDES = 100;

export type SlideRun = { text: string; bold?: boolean; italic?: boolean; underline?: boolean; color?: string; sizePt: number; font?: string };
export type SlideParagraph = {
  runs: SlideRun[];
  align?: "left" | "center" | "right" | "justify";
  bullet?: string;
  level: number;
  /** A multiple of the font size, as PowerPoint's percentage spacing; single spacing is about 1.2. */
  lineHeight: number;
};
export type SlideShape = {
  x: number;
  y: number;
  width: number;
  height: number;
  rotation?: number;
  fill?: string;
  line?: string;
  corners?: "round" | "ellipse";
  image?: string;
  anchor?: "top" | "middle" | "bottom";
  /** The text frame's margins, in EMU: left, top, right, bottom. */
  insets?: [number, number, number, number];
  /** `wrap="none"`: the text keeps to one line per paragraph. */
  noWrap?: boolean;
  paragraphs: SlideParagraph[];
  table?: SlideParagraph[][][];
  chart?: boolean;
};
export type Slide = { background?: string; backgroundImage?: string; shapes: SlideShape[] };
export type Presentation = { kind: "pptx"; width: number; height: number; slides: Slide[]; urls: string[]; truncated: boolean };

type Theme = { colors: Map<string, string>; major?: string; minor?: string };
type Part = { root: Element | null; relations: Relationships; path: string };
type Frame = { x: number; y: number; scaleX: number; scaleY: number };

const ALIASES: Record<string, string> = { tx1: "dk1", bg1: "lt1", tx2: "dk2", bg2: "lt2" };
const ALIGN: Record<string, SlideParagraph["align"]> = { l: "left", ctr: "center", r: "right", just: "justify" };
const ANCHOR: Record<string, SlideShape["anchor"]> = { t: "top", ctr: "middle", b: "bottom" };

function shiftLuminance(hex: string, modulation: number, offset: number): string {
  const [r, g, b] = [0, 2, 4].map((at) => parseInt(hex.slice(at, at + 2), 16) / 255) as [number, number, number];
  const high = Math.max(r, g, b);
  const low = Math.min(r, g, b);
  const lightness = (high + low) / 2;
  const delta = high - low;
  const saturation = delta === 0 ? 0 : delta / (1 - Math.abs(2 * lightness - 1));
  let hue = 0;
  if (delta !== 0) {
    if (high === r) hue = ((g - b) / delta) % 6;
    else if (high === g) hue = (b - r) / delta + 2;
    else hue = (r - g) / delta + 4;
  }
  const shifted = Math.min(1, Math.max(0, lightness * modulation + offset));
  const chroma = (1 - Math.abs(2 * shifted - 1)) * saturation;
  const x = chroma * (1 - Math.abs((hue % 2) - 1));
  const m = shifted - chroma / 2;
  const table: [number, number, number][] = [[chroma, x, 0], [x, chroma, 0], [0, chroma, x], [0, x, chroma], [x, 0, chroma], [chroma, 0, x]];
  const [red, green, blue] = table[Math.floor(((hue % 6) + 6) % 6)] ?? [0, 0, 0];
  return [red, green, blue].map((value) => Math.round((value + m) * 255).toString(16).padStart(2, "0")).join("");
}

function colorOf(holder: Element | null, theme: Theme): string | undefined {
  if (!holder) return undefined;
  const fill = children(holder).find((item) => ["srgbClr", "schemeClr", "sysClr"].includes(item.localName));
  if (!fill) return undefined;
  let hex: string | undefined;
  if (fill.localName === "srgbClr") hex = attribute(fill, "val") ?? undefined;
  else if (fill.localName === "sysClr") hex = attribute(fill, "lastClr") ?? undefined;
  else {
    const name = attribute(fill, "val") ?? "";
    hex = theme.colors.get(ALIASES[name] ?? name);
  }
  if (!hex || !/^[0-9A-Fa-f]{6}$/.test(hex)) return undefined;
  const scale = numberOf(child(fill, "lumMod"), "val");
  const lift = numberOf(child(fill, "lumOff"), "val");
  if (scale !== null || lift !== null) hex = shiftLuminance(hex, (scale ?? 100000) / 100000, (lift ?? 0) / 100000);
  return `#${hex}`;
}

async function readTheme(zip: Zip, master: Part): Promise<Theme> {
  const link = [...master.relations.values()].find((relation) => /theme\/theme\d*\.xml$/.test(relation.target));
  const root = link ? await part(zip, link.target) : null;
  const elements = child(root, "themeElements");
  const colors = new Map<string, string>();
  for (const color of children(child(elements, "clrScheme"))) {
    const value = children(color)[0];
    const hex = value?.localName === "sysClr" ? attribute(value, "lastClr") : attribute(value, "val");
    if (hex) colors.set(color.localName, hex);
  }
  const fonts = child(elements, "fontScheme");
  return {
    colors,
    major: attribute(child(fonts, "majorFont", "latin"), "typeface") ?? undefined,
    minor: attribute(child(fonts, "minorFont", "latin"), "typeface") ?? undefined,
  };
}

async function open(zip: Zip, path: string): Promise<Part> {
  const [root, relations] = await Promise.all([part(zip, path), relationshipsOf(zip, path)]);
  return { root, relations, path };
}

function target(owner: Part, pattern: RegExp): string | undefined {
  return [...owner.relations.values()].find((relation) => !relation.external && pattern.test(relation.target))?.target;
}

function placeholderOf(shape: Element): Element | null {
  const properties = children(shape).find((item) => item.localName.startsWith("nv"));
  return child(properties, "nvPr", "ph");
}

function matching(owner: Part | null, placeholder: Element): Element | null {
  const shapes = children(child(owner?.root, "cSld", "spTree")).filter((item) => item.localName === "sp");
  const index = attribute(placeholder, "idx");
  const type = attribute(placeholder, "type") ?? "body";
  const family = (value: string) => (value === "ctrTitle" ? "title" : value === "subTitle" ? "body" : value);
  const placed = shapes.map((shape) => ({ shape, ph: placeholderOf(shape) })).filter((item) => item.ph !== null);
  if (index !== null) {
    const same = placed.find((item) => attribute(item.ph, "idx") === index);
    if (same) return same.shape;
  }
  return placed.find((item) => family(attribute(item.ph, "type") ?? "body") === family(type))?.shape ?? null;
}

function transformOf(element: Element | null): Element | null {
  return child(element, "xfrm");
}

class SlideReader {
  constructor(
    private readonly theme: Theme,
    private readonly pictures: Pictures,
    private readonly layout: Part | null,
    private readonly master: Part | null,
  ) {}

  private styles(placeholder: Element | null, own: Element | null): (Element | null)[] {
    const type = attribute(placeholder, "type") ?? (placeholder ? "body" : "other");
    const group = type === "title" || type === "ctrTitle" ? "titleStyle" : ["body", "subTitle", "obj"].includes(type) ? "bodyStyle" : "otherStyle";
    const inherited = placeholder ? [matching(this.layout, placeholder), matching(this.master, placeholder)] : [];
    return [
      child(child(own, "txBody"), "lstStyle"),
      ...inherited.map((shape) => child(child(shape, "txBody"), "lstStyle")),
      child(child(this.master?.root, "txStyles"), group),
    ];
  }

  private paragraphs(body: Element | null, styles: (Element | null)[], heading: boolean): SlideParagraph[] {
    const paragraphs: SlideParagraph[] = [];
    // `normAutofit`: PowerPoint shrank the text to fit its box, and saved by how much.
    const autofit = child(child(body, "bodyPr"), "normAutofit");
    const scale = (numberOf(autofit, "fontScale") ?? 100000) / 100000;
    const reduction = (numberOf(autofit, "lnSpcReduction") ?? 0) / 100000;
    for (const paragraph of children(body, "p")) {
      const props = child(paragraph, "pPr");
      const level = numberOf(props, "lvl") ?? 0;
      const levels = styles.map((style) => child(style, `lvl${level + 1}pPr`));
      const defaults = levels.map((item) => child(item, "defRPr"));
      const firstValue = (read: (node: Element | null) => string | number | null | undefined) => {
        for (const node of defaults) {
          const value = read(node);
          if (value !== null && value !== undefined) return value;
        }
        return undefined;
      };
      const runs: SlideRun[] = [];
      for (const run of children(paragraph)) {
        if (run.localName === "br") {
          runs.push({ text: "\n", sizePt: 12 });
          continue;
        }
        if (run.localName !== "r" && run.localName !== "fld") continue;
        const rp = child(run, "rPr");
        const size = numberOf(rp, "sz") ?? (firstValue((node) => numberOf(node, "sz")) as number | undefined) ?? 1800;
        const typeface = attribute(child(rp, "latin"), "typeface") ?? (firstValue((node) => attribute(child(node, "latin"), "typeface")) as string | undefined);
        const bold = attribute(rp, "b") ?? (firstValue((node) => attribute(node, "b")) as string | undefined) ?? null;
        const italic = attribute(rp, "i") ?? (firstValue((node) => attribute(node, "i")) as string | undefined) ?? null;
        runs.push({
          text: child(run, "t")?.textContent ?? "",
          sizePt: (size / 100) * scale,
          bold: isOn(bold),
          italic: isOn(italic),
          underline: isOn(attribute(rp, "u")),
          color: colorOf(child(rp, "solidFill"), this.theme) ?? this.defaultColor(defaults),
          font: this.font(typeface, heading),
        });
      }
      const ownBullet = child(props, "buChar");
      const none = child(props, "buNone");
      // The nearest level that speaks decides; an empty string is "no bullet here".
      const inheritedBullet = levels
        .map((item) => (child(item, "buNone") ? "" : (attribute(child(item, "buChar"), "char") ?? undefined)))
        .find((value) => value !== undefined);
      const bullet = none ? undefined : (attribute(ownBullet, "char") ?? inheritedBullet ?? undefined);
      const percent = [props, ...levels]
        .map((item) => numberOf(child(child(item, "lnSpc"), "spcPct"), "val"))
        .find((value) => value !== null);
      paragraphs.push({
        runs,
        align: ALIGN[attribute(props, "algn") ?? levels.map((item) => attribute(item, "algn")).find(Boolean) ?? ""],
        bullet: runs.length > 0 && bullet ? "•" : undefined,
        level,
        lineHeight: Math.max(0.6, 1.2 * ((percent ?? 100000) / 100000) - reduction),
      });
    }
    return paragraphs;
  }

  private defaultColor(defaults: (Element | null)[]): string | undefined {
    for (const node of defaults) {
      const color = colorOf(child(node, "solidFill"), this.theme);
      if (color) return color;
    }
    return undefined;
  }

  private font(name: string | undefined, heading: boolean): string | undefined {
    const resolved = !name || name === "+mn-lt" ? this.theme.minor : name === "+mj-lt" ? this.theme.major : name;
    const usable = resolved ?? (heading ? this.theme.major : this.theme.minor);
    return usable && /^[\w .-]{1,64}$/.test(usable) ? usable : undefined;
  }

  private place(xfrm: Element | null, frame: Frame) {
    const offset = child(xfrm, "off");
    const extent = child(xfrm, "ext");
    if (!offset || !extent) return null;
    return {
      x: frame.x + (numberOf(offset, "x") ?? 0) * frame.scaleX,
      y: frame.y + (numberOf(offset, "y") ?? 0) * frame.scaleY,
      width: (numberOf(extent, "cx") ?? 0) * frame.scaleX,
      height: (numberOf(extent, "cy") ?? 0) * frame.scaleY,
      rotation: (numberOf(xfrm, "rot") ?? 0) / 60000 || undefined,
    };
  }

  private async image(owner: Part, fill: Element | null): Promise<string | undefined> {
    const relation = owner.relations.get(attribute(child(fill, "blip"), "embed") ?? "");
    return relation && !relation.external ? ((await this.pictures.url(relation.target)) ?? undefined) : undefined;
  }

  private async shape(element: Element, owner: Part, frame: Frame): Promise<SlideShape | null> {
    const placeholder = placeholderOf(element);
    const inherited = placeholder ? [matching(this.layout, placeholder), matching(this.master, placeholder)] : [];
    const properties = child(element, "spPr");
    const xfrm = [properties, ...inherited.map((shape) => child(shape, "spPr"))].map(transformOf).find((item) => item && child(item, "off")) ?? null;
    const placed = this.place(xfrm, frame);
    if (!placed) return null;
    const geometry = attribute(child(properties, "prstGeom"), "prst");
    const body = child(element, "txBody");
    const anchorSource = [body, ...inherited.map((shape) => child(shape, "txBody"))].map((item) => attribute(child(item, "bodyPr"), "anchor")).find(Boolean);
    const type = attribute(placeholder, "type");
    const line = child(properties, "ln");
    const bodies = [body, ...inherited.map((shape) => child(shape, "txBody"))].map((item) => child(item, "bodyPr"));
    const inset = (name: string, fallback: number) =>
      bodies.map((item) => numberOf(item, name)).find((value) => value !== null) ?? fallback;
    return {
      insets: [inset("lIns", 91440), inset("tIns", 45720), inset("rIns", 91440), inset("bIns", 45720)],
      noWrap: bodies.map((item) => attribute(item, "wrap")).find(Boolean) === "none",
      ...placed,
      fill: child(properties, "noFill") ? undefined : colorOf(child(properties, "solidFill"), this.theme),
      line: colorOf(child(line, "solidFill"), this.theme),
      corners: geometry === "ellipse" ? "ellipse" : geometry === "roundRect" ? "round" : undefined,
      anchor: ANCHOR[anchorSource ?? ""],
      paragraphs: this.paragraphs(body, this.styles(placeholder, element), type === "title" || type === "ctrTitle"),
      image: element.localName === "pic" ? await this.image(owner, child(element, "blipFill")) : undefined,
    };
  }

  private async frame(element: Element, frame: Frame): Promise<SlideShape | null> {
    const placed = this.place(child(element, "xfrm"), frame);
    if (!placed) return null;
    const data = child(element, "graphic", "graphicData");
    const table = child(data, "tbl");
    if (!table) return { ...placed, paragraphs: [], chart: true };
    const styles = [child(child(this.master?.root, "txStyles"), "otherStyle")];
    const rows = children(table, "tr").map((row) =>
      children(row, "tc").map((cell) => this.paragraphs(child(cell, "txBody"), styles, false)),
    );
    return { ...placed, paragraphs: [], table: rows };
  }

  async tree(owner: Part | null, frame: Frame, root: Element | null, skipPlaceholders: boolean): Promise<SlideShape[]> {
    const shapes: SlideShape[] = [];
    for (const element of children(root)) {
      if (element.localName === "sp" || element.localName === "pic") {
        if (skipPlaceholders && placeholderOf(element)) continue;
        if (owner) {
          const shape = await this.shape(element, owner, frame);
          if (shape) shapes.push(shape);
        }
      } else if (element.localName === "graphicFrame") {
        const shape = await this.frame(element, frame);
        if (shape) shapes.push(shape);
      } else if (element.localName === "grpSp") {
        const xfrm = child(child(element, "grpSpPr"), "xfrm");
        const off = child(xfrm, "off");
        const ext = child(xfrm, "ext");
        const chOff = child(xfrm, "chOff");
        const chExt = child(xfrm, "chExt");
        const scaleX = (numberOf(ext, "cx") ?? 1) / (numberOf(chExt, "cx") || 1);
        const scaleY = (numberOf(ext, "cy") ?? 1) / (numberOf(chExt, "cy") || 1);
        shapes.push(
          ...(await this.tree(
            owner,
            {
              x: frame.x + ((numberOf(off, "x") ?? 0) - (numberOf(chOff, "x") ?? 0) * scaleX) * frame.scaleX,
              y: frame.y + ((numberOf(off, "y") ?? 0) - (numberOf(chOff, "y") ?? 0) * scaleY) * frame.scaleY,
              scaleX: frame.scaleX * scaleX,
              scaleY: frame.scaleY * scaleY,
            },
            element,
            skipPlaceholders,
          )),
        );
      }
    }
    return shapes;
  }

  async background(owners: (Part | null)[]): Promise<Pick<Slide, "background" | "backgroundImage">> {
    for (const owner of owners) {
      const fill = child(child(owner?.root, "cSld", "bg"), "bgPr");
      if (!fill || !owner) continue;
      const image = await this.image(owner, child(fill, "blipFill"));
      if (image) return { backgroundImage: image };
      const color = colorOf(child(fill, "solidFill"), this.theme);
      if (color) return { background: color };
    }
    return {};
  }
}

const IDENTITY: Frame = { x: 0, y: 0, scaleX: 1, scaleY: 1 };

export async function readPresentation(zip: Zip): Promise<Presentation> {
  const path = "ppt/presentation.xml";
  const [root, relations] = await Promise.all([part(zip, path), relationshipsOf(zip, path)]);
  const size = child(root, "sldSz");
  const listed = children(child(root, "sldIdLst"), "sldId");
  const pictures = new Pictures(zip);
  const slides: Slide[] = [];
  for (const item of listed.slice(0, MAX_SLIDES)) {
    const slideTarget = relations.get(relationIdOf(item));
    if (!slideTarget || slideTarget.external) continue;
    const slide = await open(zip, slideTarget.target);
    const layoutPath = target(slide, /slideLayouts\//);
    const layout = layoutPath ? await open(zip, layoutPath) : null;
    const masterPath = layout ? target(layout, /slideMasters\//) : undefined;
    const master = masterPath ? await open(zip, masterPath) : null;
    const theme = master ? await readTheme(zip, master) : { colors: new Map<string, string>() };
    const reader = new SlideReader(theme, pictures, layout, master);
    const tree = (owner: Part | null) => child(owner?.root, "cSld", "spTree");
    slides.push({
      ...(await reader.background([slide, layout, master])),
      shapes: [
        ...(await reader.tree(master, IDENTITY, tree(master), true)),
        ...(await reader.tree(layout, IDENTITY, tree(layout), true)),
        ...(await reader.tree(slide, IDENTITY, tree(slide), false)),
      ],
    });
  }
  return {
    kind: "pptx",
    width: numberOf(size, "cx") ?? 12_192_000,
    height: numberOf(size, "cy") ?? 6_858_000,
    slides,
    urls: pictures.urls,
    truncated: listed.length > MAX_SLIDES,
  };
}
