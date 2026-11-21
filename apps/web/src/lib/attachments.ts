// SPDX-License-Identifier: Apache-2.0
/**
 * Files a person hands to a work item (ADR 0201): what the workflow declares in
 * `metadata.attachments`, which of a person's files fit it, and which artifacts on a work item's
 * page are handed files. The API is the judge of every limit; this is the courtesy before the send.
 */
import { humanSize } from "./artifacts";
import type { Artifact } from "./types";

/** `metadata.attachments` with its defaults filled in (ADR 0201 §1). */
export type AttachmentRule = { accept: string[]; min: number; max: number };

/** The extensions each artifact type is recognised by, as the API reads a file name (ADR 0201 §3). */
const EXTENSIONS: Record<string, string[]> = {
  markdown: ["md", "markdown"],
  json: ["json"],
  mermaid: ["mmd"],
  sarif: ["sarif"],
  text: ["txt", "text", "log"],
  csv: ["csv"],
  html: ["html", "htm"],
  pdf: ["pdf"],
  docx: ["docx"],
  xlsx: ["xlsx"],
  pptx: ["pptx"],
  png: ["png"],
  jpeg: ["jpg", "jpeg"],
  gif: ["gif"],
  webp: ["webp"],
  svg: ["svg"],
  mp4: ["mp4"],
  webm: ["webm"],
  diff: ["diff", "patch"],
};

/** What a workflow declares for the files of a work item, or null when it takes none from a person. */
export function attachmentRule(metadata: { attachments?: unknown } | null | undefined): AttachmentRule | null {
  const declared = metadata?.attachments as { accept?: string[]; min?: number; max?: number } | undefined;
  if (!declared || !Array.isArray(declared.accept)) return null;
  return { accept: [...declared.accept], min: declared.min ?? 0, max: declared.max ?? 20 };
}

/** The `accept` attribute of a file input for these artifact types. */
export function acceptAttribute(rule: AttachmentRule): string {
  return rule.accept
    .flatMap((type) => EXTENSIONS[type] ?? [])
    .map((extension) => `.${extension}`)
    .join(",");
}

/** The sentence that states what the drop zone takes. */
export function acceptedSentence(rule: AttachmentRule): string {
  const bounds = rule.min > 0 ? `at least ${rule.min} and at most ${rule.max} files` : `up to ${rule.max} files`;
  return `Accepted types: ${rule.accept.join(", ")}; ${bounds}.`;
}

/** Why this file cannot be given to the work item, or null. */
export function fileRefusal(name: string, size: number, rule: AttachmentRule, maxBytes?: number): string | null {
  const extension = name.includes(".") ? name.split(".").at(-1)!.toLowerCase() : "";
  const allowed = rule.accept.flatMap((type) => EXTENSIONS[type] ?? []);
  if (!allowed.includes(extension)) return `${name}: this file type is not accepted (${rule.accept.join(", ")})`;
  if (maxBytes !== undefined && size > maxBytes) return `${name}: this file is larger than ${humanSize(maxBytes)}`;
  return null;
}

/** The handed files of a work item: the latest revision of each name that a person or an application handed. */
export function handedFiles(artifacts: Artifact[]): Artifact[] {
  const latest = new Map<string, Artifact>();
  for (const artifact of artifacts) {
    if (!artifact.handed_by) continue;
    const before = latest.get(artifact.name);
    if (!before || before.version < artifact.version) latest.set(artifact.name, artifact);
  }
  return [...latest.values()].sort((a, b) => a.name.localeCompare(b.name));
}
