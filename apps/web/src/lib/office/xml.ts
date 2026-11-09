// SPDX-License-Identifier: Apache-2.0
/** The small vocabulary every Office part is read with: elements by local name, relationships, images. */
import { OfficeFileError, type Zip } from "./zip";

export function parseXml(text: string): Element {
  const document = new DOMParser().parseFromString(text, "application/xml");
  if (document.getElementsByTagName("parsererror").length > 0) throw new OfficeFileError("damaged file");
  return document.documentElement;
}

export async function part(zip: Zip, path: string): Promise<Element | null> {
  const text = await zip.text(path);
  return text === null ? null : parseXml(text);
}

export function children(element: Element | null | undefined, name?: string): Element[] {
  return element ? Array.from(element.children).filter((child) => !name || child.localName === name) : [];
}

export function child(element: Element | null | undefined, ...path: string[]): Element | null {
  let current: Element | null = element ?? null;
  for (const name of path) current = current ? (children(current, name)[0] ?? null) : null;
  return current;
}

export function descendants(element: Element | null | undefined, name: string): Element[] {
  return element ? Array.from(element.getElementsByTagName("*")).filter((node) => node.localName === name) : [];
}

export function attribute(element: Element | null | undefined, name: string): string | null {
  if (!element) return null;
  for (const item of Array.from(element.attributes)) if (item.localName === name) return item.value;
  return null;
}

/** `r:id`, the link to a relationship: an element can also hold a plain `id` of its own. */
export function relationIdOf(element: Element | null | undefined): string {
  if (!element) return "";
  for (const item of Array.from(element.attributes)) if (item.localName === "id" && item.prefix) return item.value;
  return "";
}

export function numberOf(element: Element | null | undefined, name: string): number | null {
  const value = attribute(element, name);
  if (value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** A switch like `b="1"`, `<w:b/>` or `<w:b w:val="false"/>`. */
export function isOn(value: string | null): boolean {
  return value !== null && !["0", "false", "off", "none"].includes(value);
}

export type Relationships = Map<string, { target: string; external: boolean }>;

function resolve(from: string, target: string): string {
  if (target.startsWith("/")) return target.slice(1);
  const parts = from.split("/").slice(0, -1);
  for (const piece of target.split("/")) {
    if (piece === "..") parts.pop();
    else if (piece !== ".") parts.push(piece);
  }
  return parts.join("/");
}

/** What a part links to. An external target is kept as such and never followed. */
export async function relationshipsOf(zip: Zip, path: string): Promise<Relationships> {
  const slash = path.lastIndexOf("/");
  const root = await part(zip, `${path.slice(0, slash + 1)}_rels/${path.slice(slash + 1)}.rels`);
  const found: Relationships = new Map();
  for (const relation of children(root, "Relationship")) {
    const external = attribute(relation, "TargetMode") === "External";
    const target = attribute(relation, "Target") ?? "";
    found.set(attribute(relation, "Id") ?? "", { target: external ? target : resolve(path, target), external });
  }
  return found;
}

const MAX_IMAGES = 200;

function imageType(data: Uint8Array): string | null {
  if (data[0] === 0x89 && data[1] === 0x50 && data[2] === 0x4e && data[3] === 0x47) return "image/png";
  if (data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) return "image/jpeg";
  if (data[0] === 0x47 && data[1] === 0x49 && data[2] === 0x46) return "image/gif";
  if (data[0] === 0x52 && data[1] === 0x49 && data[2] === 0x46 && data[8] === 0x57 && data[9] === 0x45) return "image/webp";
  return null;
}

/**
 * The object URLs of a file's pictures. The type comes from the bytes, never from the file's name, and
 * only raster formats pass: an SVG can carry script.
 */
export class Pictures {
  readonly urls: string[] = [];
  private readonly seen = new Map<string, string | null>();

  constructor(private readonly zip: Zip) {}

  async url(path: string | undefined): Promise<string | null> {
    if (!path) return null;
    const known = this.seen.get(path);
    if (known !== undefined) return known;
    if (this.urls.length >= MAX_IMAGES) return null;
    const data = await this.zip.bytes(path);
    const type = data && imageType(data);
    const url = data && type ? URL.createObjectURL(new Blob([data as BlobPart], { type })) : null;
    if (url) this.urls.push(url);
    this.seen.set(path, url);
    return url;
  }
}
