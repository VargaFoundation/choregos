// SPDX-License-Identifier: Apache-2.0
/** What the knowledge pages say about a base and its documents (ADR 0051). */
import { relative } from "./format";
import type { KnowledgeBase, KnowledgeEvalCase, KnowledgeSource } from "./types";

/** Where a base is served, said in words: who reads it, which runs get it. */
export function scopeLabel(base: Pick<KnowledgeBase, "scope" | "projects" | "groups">): string {
  if (base.scope === "projects") return `projects: ${(base.projects ?? []).join(", ")}`;
  if (base.scope === "groups") return `groups: ${(base.groups ?? []).join(", ")}`;
  return "whole organisation";
}

/** Whether a project's runs are served this base: an organisation-wide one, or one that names the project. */
export function servedToProject(base: Pick<KnowledgeBase, "scope" | "projects">, slug: string): boolean {
  return base.scope === "org" || (base.scope === "projects" && (base.projects ?? []).includes(slug));
}

/** Whether a base is the project's own: created from it, so its owners curate it (`owner_project`). */
export function ownedByProject(base: Pick<KnowledgeBase, "owner_project">, slug: string): boolean {
  return base.owner_project === slug;
}

/** A base the project is served without owning: an organisation's base scoped to several projects. */
export function alsoServedToProject(base: Pick<KnowledgeBase, "scope" | "projects" | "owner_project">, slug: string): boolean {
  return base.scope === "projects" && (base.projects ?? []).includes(slug) && !ownedByProject(base, slug);
}

/** The registry's "Used by", in words: who is served, with the projects' names where known. */
export function usedByLabel(
  base: Pick<KnowledgeBase, "scope" | "projects" | "groups">,
  projectNames: Record<string, string> = {},
): string {
  if (base.scope === "projects") return (base.projects ?? []).map((p) => projectNames[p] ?? p).join(", ");
  if (base.scope === "groups") return `People in groups ${(base.groups ?? []).join(", ")} (never agents)`;
  return "Every project";
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
  kind: "sharepoint" | "confluence" | "web" | "git",
  fields: { site: string; folder: string; url: string; ref: string; paths: string; space?: string; pageId?: string; attachments?: boolean; exclude?: string },
): Record<string, unknown> {
  if (kind === "confluence") {
    const page = (fields.pageId ?? "").trim();
    return { space_key: (fields.space ?? "").trim(), ...(page ? { page_id: page } : {}), include_attachments: Boolean(fields.attachments) };
  }
  if (kind === "sharepoint") {
    const site = fields.site.trim();
    const key = site.startsWith("b!") ? "drive_id" : "site_id";
    return { [key]: site, ...(fields.folder.trim() ? { folder: fields.folder.trim() } : {}) };
  }
  if (kind === "web") {
    const exclude = (fields.exclude ?? "").split(",").map((p) => p.trim()).filter(Boolean);
    return { url: fields.url.trim(), max_depth: 2, max_pages: 50, ...(exclude.length ? { exclude } : {}) };
  }
  return {
    url: fields.url.trim(),
    ref: fields.ref.trim() || "main",
    paths: fields.paths.split(",").map((p) => p.trim()).filter(Boolean),
  };
}

const FAILURES: Record<string, string> = {
  too_large: "the base is too large to read in one distillation: leave the biggest documents out",
  over_budget: "stopped before the budget was spent",
  no_document: "no converted document to distil",
  model_refused: "the model is not allowed to distil (see /admin/models)",
  nothing_cited: "no principle cited a document of the base, so nothing was published",
  gateway: "the model gateway failed",
  cancelled: "cancelled",
};

/** "map 3/8 - $0.92 spent": where a running distillation is. */
export function distillationProgress(d: Record<string, unknown>): string {
  const total = Number(d.parts_total ?? 0);
  const phase = String(d.phase ?? "plan");
  const where = phase === "map" && total ? `map ${Number(d.parts_done ?? 0)}/${total}` : phase;
  const spent = Number(d.spent_usd ?? 0);
  return spent > 0 ? `${where} \u00b7 $${spent.toFixed(2)} spent` : where;
}

/** The last distillation, in words, and the skill version it waits on. */
export function distillationLabel(d: Record<string, unknown> | null | undefined): string {
  if (!d) return "not distilled";
  if (d.status === "running") {
    const progress = d.phase && d.phase !== "plan" ? ` ${distillationProgress(d)}` : "";
    return `distilling\u2026${progress}`;
  }
  if (d.status === "failed") {
    const why = FAILURES[String(d.code ?? "")];
    const detail = String(d.error ?? "");
    const spent = Number(d.spent_usd ?? 0);
    return `failed: ${why ? `${why} (${detail})` : detail}${spent > 0 ? ` \u00b7 $${spent.toFixed(2)} spent` : ""}`;
  }
  const dropped = Array.isArray(d.dropped) ? d.dropped.length : 0;
  return `${String(d.skill)} v${String(d.version)}: ${String(d.principles)} principles, waiting for review${dropped ? ` (${dropped} dropped: no source)` : ""}`;
}

/** How a source refreshes, in words: `every 60 min`, `cron 0 6 * * 1-5 (Europe/Paris)` or `manual`. */
export function refreshLabel(
  source: Pick<KnowledgeSource, "refresh_mode" | "refresh_cron" | "refresh_timezone" | "interval_minutes">,
): string {
  if (source.refresh_mode === "manual") return "manual";
  if (source.refresh_mode === "cron") return `cron ${source.refresh_cron} (${source.refresh_timezone ?? "UTC"})`;
  return `every ${source.interval_minutes} min`;
}

/** When it syncs next (an estimate: the worker may be down): `next in 23 minutes`; manual → `—`. */
export function nextSyncLabel(source: Pick<KnowledgeSource, "refresh_mode" | "next_sync_at">): string {
  if (source.refresh_mode === "manual") return "—";
  return source.next_sync_at ? `next ${relative(source.next_sync_at)}` : "due now";
}

/** The refresh as the console edits it. */
export type RefreshDraft = {
  mode: "interval" | "cron" | "manual";
  interval: string;
  cron: string;
  timezone: string;
};

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function draftOf(source?: Partial<KnowledgeSource>): RefreshDraft {
  return {
    mode: source?.refresh_mode ?? "interval",
    interval: String(source?.interval_minutes ?? 60),
    cron: source?.refresh_cron ?? "",
    timezone: source?.refresh_timezone && source.refresh_timezone !== "UTC" ? source.refresh_timezone : browserTimezone(),
  };
}

export type RefreshBody = {
  refresh_mode: "interval" | "cron" | "manual";
  interval_minutes?: number;
  refresh_cron?: string;
  refresh_timezone?: string;
};

/** The body of a create or a patch: only the fields the mode uses. */
export function refreshBody(draft: RefreshDraft): RefreshBody {
  if (draft.mode === "cron") return { refresh_mode: "cron", refresh_cron: draft.cron.trim(), refresh_timezone: draft.timezone };
  if (draft.mode === "manual") return { refresh_mode: "manual" };
  return { refresh_mode: "interval", interval_minutes: Number(draft.interval) || 60 };
}
