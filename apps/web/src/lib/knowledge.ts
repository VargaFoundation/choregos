// SPDX-License-Identifier: Apache-2.0
/** What the knowledge pages say about a base and its documents (ADR 0051). */
import { relative } from "./format";
import type { KnowledgeBase, KnowledgeEvalCase, KnowledgeGrant, KnowledgePermissions, KnowledgeSource } from "./types";

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

/** The documents a sync converted with part of their content left out, one sentence each. */
export function truncationsOf(summary: Record<string, unknown> | null | undefined): string[] {
  const reasons = summary?.truncated_reasons;
  return Array.isArray(reasons) ? reasons.map(String) : [];
}

/** What a sync did, in words: `2 added · 1 deleted`; nothing to say → `no change`. */
export function syncSummary(summary: Record<string, unknown> | null | undefined): string {
  if (!summary) return "never synced";
  const parts = ["added", "updated", "deleted", "skipped", "truncated"]
    .filter((k) => Number(summary[k] ?? 0) > 0)
    .map((k) => `${Number(summary[k])} ${k}`);
  return parts.length ? parts.join(" · ") : "no change";
}

/**
 * The page ids in a field: numbers, or Confluence page addresses (`…/pages/<id>/Title`, `…pageId=<id>`),
 * separated by commas, spaces or lines. Order kept, repeats dropped.
 */
export function pageIdsOf(text: string): string[] {
  const found: string[] = [];
  for (const token of text.split(/[\s,;]+/).filter(Boolean)) {
    const id = /^\d+$/.test(token) ? token : (token.match(/\/pages\/(\d+)/) ?? token.match(/[?&]pageId=(\d+)/))?.[1];
    if (id && !found.includes(id)) found.push(id);
  }
  return found;
}

/** The configuration a source form sends, by kind. */
export function sourceConfig(
  kind: "sharepoint" | "confluence" | "web" | "git",
  fields: { site: string; folder: string; url: string; ref: string; paths: string; space?: string; pageId?: string; attachments?: boolean; exclude?: string; descendants?: boolean; pageOnly?: boolean; depth?: string; maxPages?: string; documents?: boolean },
): Record<string, unknown> {
  if (kind === "confluence") {
    const ids = pageIdsOf(fields.pageId ?? "");
    const pages = ids.length === 1 ? { page_id: ids[0] } : ids.length > 1 ? { page_ids: ids } : {};
    // Sub-pages are the default; only "no" is sent, and only when a page was chosen.
    const alone = ids.length > 0 && fields.descendants === false ? { descendants: false } : {};
    return { space_key: (fields.space ?? "").trim(), ...pages, ...alone, include_attachments: Boolean(fields.attachments) };
  }
  if (kind === "sharepoint") {
    const site = fields.site.trim();
    const key = site.startsWith("b!") ? "drive_id" : "site_id";
    return { [key]: site, ...(fields.folder.trim() ? { folder: fields.folder.trim() } : {}) };
  }
  if (kind === "web") {
    const exclude = (fields.exclude ?? "").split(",").map((p) => p.trim()).filter(Boolean);
    // "This page only" is a crawl of depth 0 and one page; otherwise the depth and cap the form shows.
    const bounded = (raw: string | undefined, fallback: number, low: number, high: number) => {
      const n = Number.parseInt((raw ?? "").trim(), 10);
      return Number.isFinite(n) ? Math.max(low, Math.min(high, n)) : fallback;
    };
    const pageOnly = Boolean(fields.pageOnly);
    return {
      url: fields.url.trim(),
      max_depth: pageOnly ? 0 : bounded(fields.depth, 2, 0, 5),
      max_pages: pageOnly ? 1 : bounded(fields.maxPages, 50, 1, 500),
      ...(exclude.length ? { exclude } : {}),
      ...(fields.documents ? { include_documents: true } : {}),
    };
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

const EVERYTHING: KnowledgePermissions = { edit: true, distill: true, share: true, archive: true, widen: true };

/**
 * What the caller may do with a base, as the server said (`can`, S42-11). A response without it
 * (an older API) allows everything: the console never hides a gesture on a guess, and the server
 * refuses what the caller may not do anyway.
 */
export function permissionsOf(base: Pick<KnowledgeBase, "can">): KnowledgePermissions {
  return base.can ?? EVERYTHING;
}

/** Who may read the base, in words: the reach, and whether people need a grant. */
export function accessLabel(base: Pick<KnowledgeBase, "restricted" | "scope" | "projects" | "groups">): string {
  return base.restricted
    ? "restricted: people read it through a grant; its runs are still served"
    : `readable by ${scopeLabel(base)}`;
}

export const GRANT_ROLES: KnowledgeGrant["role"][] = ["viewer", "editor", "owner"];

/** What a role lets one do, for the role select's hint. */
export function roleHint(role: KnowledgeGrant["role"] | string | null | undefined): string {
  if (role === "owner") return "feeds, distils, shares and archives";
  if (role === "editor") return "feeds, syncs and distils";
  if (role === "viewer") return "reads";
  return "reads through the base's reach";
}

/** A grant's name on screen: the server's display, else its subject. */
export function grantName(grant: Pick<KnowledgeGrant, "subject" | "display" | "subject_type">): string {
  return grant.display || grant.subject;
}

/** The key of a grant in a list: one per (type, subject). */
export const grantKey = (g: Pick<KnowledgeGrant, "subject_type" | "subject">): string => `${g.subject_type}:${g.subject}`;

/** The grants as the PUT sends them: only what the server reads. */
export function grantsBody(grants: KnowledgeGrant[]): KnowledgeGrant[] {
  return grants.map((g) => ({ subject_type: g.subject_type, subject: g.subject, role: g.role }));
}
