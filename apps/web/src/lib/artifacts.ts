// SPDX-License-Identifier: Apache-2.0
/**
 * Artifacts of a work item (ADR 0049): grouped by name, the viewer each one gets, and what the
 * hand-off receipts say about its revisions.
 */
import type { Artifact, HandOff } from "./types";

/** One named output and all its revisions, latest last. */
export type ArtifactGroup = { name: string; latest: Artifact; revisions: Artifact[] };

export function groupArtifacts(artifacts: Artifact[]): ArtifactGroup[] {
  const byName = new Map<string, Artifact[]>();
  for (const artifact of artifacts) {
    byName.set(artifact.name, [...(byName.get(artifact.name) ?? []), artifact]);
  }
  return [...byName.entries()]
    .map(([name, revisions]) => {
      const sorted = [...revisions].sort((a, b) => a.version - b.version);
      return { name, latest: sorted.at(-1)!, revisions: sorted };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * How an artifact is shown. Only formats the console can render without running the agent's
 * content: text as text, markdown by a renderer that builds elements (no HTML), images in `<img>`.
 * Everything else is downloaded.
 */
export type ViewerKind = "markdown" | "sarif" | "json" | "text" | "image" | "download";

export function viewerOf(artifact: Pick<Artifact, "type" | "kind" | "media_type">): ViewerKind {
  if (artifact.type === "sarif") return "sarif";
  if (artifact.type === "markdown") return "markdown";
  if (artifact.type === "json") return "json";
  if (artifact.type === "text" || artifact.type === "csv" || artifact.type === "mermaid") return "text";
  // SVG can carry script: shown as an image only, where a browser never runs it.
  if (artifact.media_type.startsWith("image/")) return "image";
  return "download";
}

/** The file name a download gets: the name with its type's extension. */
const EXTENSIONS: Record<string, string> = {
  markdown: "md",
  json: "json",
  mermaid: "mmd",
  sarif: "sarif",
  text: "txt",
  csv: "csv",
  html: "html",
  pdf: "pdf",
  docx: "docx",
  xlsx: "xlsx",
  pptx: "pptx",
  png: "png",
  jpeg: "jpg",
  gif: "gif",
  webp: "webp",
  svg: "svg",
  mp4: "mp4",
  webm: "webm",
};

export function filenameOf(artifact: Pick<Artifact, "name" | "type">): string {
  const extension = EXTENSIONS[artifact.type];
  if (!extension || artifact.name.toLowerCase().endsWith(`.${extension}`)) return artifact.name;
  return `${artifact.name}.${extension}`;
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

/** The runs that read a revision, from the hand-off receipts (S25-04) — matched by the run that
 * produced it (an inline value's receipt keeps its canonical-JSON digest) or by its digest (a file). */
export function readersOf(artifact: Artifact, handOffs: HandOff[]): { run_id: string; stage?: string | null }[] {
  const passage = handOffs.find((h) => h.output === artifact.name);
  if (!passage) return [];
  const produced = passage.events.find(
    (e) => e.kind === "produced" && (e.digest === artifact.digest || e.run_id === artifact.produced_by.run_id),
  );
  const digest = produced?.digest ?? artifact.digest;
  return passage.events
    .filter((e) => e.kind === "read" && e.digest === digest)
    .map((e) => ({ run_id: e.run_id, stage: e.stage }));
}
