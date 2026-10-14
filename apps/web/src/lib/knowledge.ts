// SPDX-License-Identifier: Apache-2.0
/** What the knowledge pages say about a base and its documents (ADR 0051). */
import type { KnowledgeBase, KnowledgeEvalCase } from "./types";

/** Where a base is served, said in words: who reads it, which runs get it. */
export function scopeLabel(base: Pick<KnowledgeBase, "scope" | "projects" | "groups">): string {
  if (base.scope === "projects") return `projects: ${(base.projects ?? []).join(", ")}`;
  if (base.scope === "groups") return `groups: ${(base.groups ?? []).join(", ")}`;
  return "whole organisation";
}

/** `{"ready": 3, "failed": 1}` → `3 ready · 1 failed`. */
export function statusSummary(counts: Record<string, number> | undefined): string {
  const order = ["ready", "pending", "converting", "failed"];
  const parts = order.filter((s) => (counts ?? {})[s]).map((s) => `${counts![s]} ${s}`);
  return parts.length ? parts.join(" · ") : "no document";
}

/** Whether a conversion still runs in the background (the page then polls). */
export function stillConverting(base: Pick<KnowledgeBase, "documents_by_status"> | undefined): boolean {
  const counts = base?.documents_by_status ?? {};
  return Boolean(counts.pending || counts.converting);
}

/** How the index ranks here, in words: the page says when pgvector is missing. */
export function searchModeLabel(mode: string | undefined): string {
  if (mode === "hybrid") return "hybrid — full-text and embeddings (pgvector)";
  if (mode === "full-text") return "full-text only — pgvector is not installed, or no embedding model";
  if (mode === "in-process") return "in-process (development database)";
  return mode ?? "—";
}

/** One question per line: `question | expected.docx, other.pdf`. Lines without `|` are ignored. */
export function parseEvalSet(text: string): KnowledgeEvalCase[] {
  return text
    .split("\n")
    .map((line) => line.split("|"))
    .filter((parts) => parts.length === 2 && parts[0]!.trim() && parts[1]!.trim())
    .map(([question, expected]) => ({
      question: question!.trim(),
      expected: expected!.split(",").map((e) => e.trim()).filter(Boolean),
    }));
}

export function formatEvalSet(cases: KnowledgeEvalCase[] | undefined): string {
  return (cases ?? []).map((c) => `${c.question} | ${c.expected.join(", ")}`).join("\n");
}

/** `0.8` → `80 %`; nothing measured yet → `not measured`. */
export function recallLabel(recall: number | null | undefined): string {
  return recall === null || recall === undefined ? "not measured" : `${Math.round(recall * 100)} %`;
}

/** What a sync did, in words: `2 added · 1 deleted`; nothing to say → `no change`. */
export function syncSummary(summary: Record<string, unknown> | null | undefined): string {
  if (!summary) return "never synced";
  const parts = ["added", "updated", "deleted", "skipped"]
    .filter((k) => Number(summary[k] ?? 0) > 0)
    .map((k) => `${Number(summary[k])} ${k}`);
  return parts.length ? parts.join(" · ") : "no change";
}

/** The configuration a source form sends, by kind. */
export function sourceConfig(
  kind: "sharepoint" | "web" | "git",
  fields: { site: string; folder: string; url: string; ref: string; paths: string },
): Record<string, unknown> {
  if (kind === "sharepoint") {
    const site = fields.site.trim();
    const key = site.startsWith("b!") ? "drive_id" : "site_id";
    return { [key]: site, ...(fields.folder.trim() ? { folder: fields.folder.trim() } : {}) };
  }
  if (kind === "web") return { url: fields.url.trim(), max_depth: 2, max_pages: 50 };
  return {
    url: fields.url.trim(),
    ref: fields.ref.trim() || "main",
    paths: fields.paths.split(",").map((p) => p.trim()).filter(Boolean),
  };
}

/** The last distillation, in words, and the skill version it waits on. */
export function distillationLabel(d: Record<string, unknown> | null | undefined): string {
  if (!d) return "not distilled";
  if (d.status === "running") return "distilling…";
  if (d.status === "failed") return `failed: ${String(d.error ?? "")}`;
  const dropped = Array.isArray(d.dropped) ? d.dropped.length : 0;
  return `${String(d.skill)} v${String(d.version)}: ${String(d.principles)} principles, waiting for review${dropped ? ` (${dropped} dropped: no source)` : ""}`;
}
