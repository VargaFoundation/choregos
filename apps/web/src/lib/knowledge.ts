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

/** `Accept-Language: fr` lines → `{"Accept-Language": "fr"}`; a line without `:` is ignored, the last of a name wins. */
export function headersOf(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const at = line.indexOf(":");
    if (at <= 0) continue;
    const name = line.slice(0, at).trim();
    if (name) out[name] = line.slice(at + 1).trim();
  }
  return out;
}

/** The names a credential travels under: the API refuses them as plain headers (a connector holds them). */
export const CREDENTIAL_HEADER = /auth|cookie|token|key|secret|session|password|signature/i;

/** The configuration a source form sends, by kind. */
export function sourceConfig(
  kind: "sharepoint" | "confluence" | "web" | "git",
  fields: { site: string; folder: string; url: string; ref: string; paths: string; space?: string; pageId?: string; attachments?: boolean; exclude?: string; descendants?: boolean; pageOnly?: boolean; depth?: string; maxPages?: string; documents?: boolean; headers?: string },
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
      ...(Object.keys(headersOf(fields.headers ?? "")).length ? { headers: headersOf(fields.headers ?? "") } : {}),
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
  if (d.status === "stopped") {
    const spent = Number(d.spent_usd ?? 0);
    const by = d.stopped_by ? ` by ${String(d.stopped_by)}` : "";
    return `stopped${by}${spent > 0 ? ` \u00b7 $${spent.toFixed(2)} spent` : ""}`;
  }
  const dropped = Array.isArray(d.dropped) ? d.dropped.length : 0;
  return `${String(d.skill)} v${String(d.version)}: ${String(d.principles)} principles, waiting for review${dropped ? ` (${dropped} dropped: no source)` : ""}`;
}

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
/** The days of a weekly refresh, in the console's order (Monday first); cron numbers them from Sunday = 0. */
export const WEEK: { day: number; short: string }[] = [1, 2, 3, 4, 5, 6, 0].map((day) => ({ day, short: WEEKDAYS[day]!.slice(0, 3) }));

const pad = (n: number) => String(n).padStart(2, "0");

/** A cron the console offers as a choice, read back from its five fields; anything else stays a cron. */
function presetOf(cron: string): Pick<RefreshDraft, "mode" | "time" | "days" | "dayOfMonth"> | null {
  const match = cron.trim().split(/\s+/);
  if (match.length !== 5) return null;
  const [minute, hour, dom, month, dow] = match as [string, string, string, string, string];
  if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}$/.test(hour) || month !== "*") return null;
  const m = Number(minute);
  const h = Number(hour);
  if (m > 59 || h > 23) return null;
  const time = `${pad(h)}:${pad(m)}`;
  const base = { time, days: [1], dayOfMonth: "1" };
  if (dom === "*" && dow === "*") return { ...base, mode: "daily" };
  if (dom === "*" && dow === "1-5") return { ...base, mode: "weekdays" };
  if (dom === "*" && /^[0-6](,[0-6])*$/.test(dow)) return { ...base, mode: "weekly", days: [...new Set(dow.split(",").map(Number))] };
  if (/^\d{1,2}$/.test(dom) && Number(dom) >= 1 && Number(dom) <= 28 && dow === "*") return { ...base, mode: "monthly", dayOfMonth: dom };
  return null;
}

/** `every 2 hours`, `every day at 06:00`, `every Monday and Thursday at 18:30`: a refresh, said in words. */
export function describeRefresh(draft: RefreshDraft): string {
  const at = ` at ${draft.time || "00:00"}`;
  switch (draft.mode) {
    case "manual":
      return "only when someone presses sync now";
    case "minutes":
      return `every ${Number(draft.interval) || 60} minutes`;
    case "hours": {
      const n = Number(draft.hours) || 1;
      return n === 1 ? "every hour" : `every ${n} hours`;
    }
    case "daily":
      return `every day${at}`;
    case "weekdays":
      return `every weekday (Monday to Friday)${at}`;
    case "weekly": {
      const names = WEEK.filter((w) => draft.days.includes(w.day)).map((w) => WEEKDAYS[w.day]!);
      const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0] ?? "Monday";
      return `every ${list}${at}`;
    }
    case "monthly":
      return `on day ${Number(draft.dayOfMonth) || 1} of every month${at}`;
    default:
      return `on the cron ${draft.cron.trim() || "…"}`;
  }
}

/** How a source refreshes, in words: `every 60 min`, `every weekday … at 06:00 (Europe/Paris)`, `cron …` or `manual`. */
export function refreshLabel(
  source: Pick<KnowledgeSource, "refresh_mode" | "refresh_cron" | "refresh_timezone" | "interval_minutes">,
): string {
  if (source.refresh_mode === "manual") return "manual";
  if (source.refresh_mode === "cron") {
    const zone = source.refresh_timezone ?? "UTC";
    const preset = presetOf(source.refresh_cron ?? "");
    return preset ? `${describeRefresh({ ...draftOf(), ...preset })} (${zone})` : `cron ${source.refresh_cron} (${zone})`;
  }
  return `every ${source.interval_minutes} min`;
}

/** When it syncs next (an estimate: the worker may be down): `next in 23 minutes`; manual → `—`. */
export function nextSyncLabel(source: Pick<KnowledgeSource, "refresh_mode" | "next_sync_at">): string {
  if (source.refresh_mode === "manual") return "—";
  return source.next_sync_at ? `next ${relative(source.next_sync_at)}` : "due now";
}

/**
 * The refresh as the console edits it: a choice a person reads (every N hours, every weekday at 06:00…)
 * that the API stores as one of its three modes — an interval, a cron in a time zone, or manual.
 */
export type RefreshDraft = {
  mode: "minutes" | "hours" | "daily" | "weekdays" | "weekly" | "monthly" | "cron" | "manual";
  /** `minutes`: every N minutes, at least 5. */
  interval: string;
  /** `hours`: every N hours. */
  hours: string;
  /** `daily`, `weekdays`, `weekly`, `monthly`: the time of day, `HH:MM`. */
  time: string;
  /** `weekly`: the days, cron numbers (Sunday = 0). */
  days: number[];
  /** `monthly`: the day of the month, 1 to 28 (every month has it). */
  dayOfMonth: string;
  /** `cron`: five fields, as typed. */
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

/** Whether a mode is a time of day, read in a time zone (the cron-backed choices). */
export function inTimezone(mode: RefreshDraft["mode"]): boolean {
  return mode !== "minutes" && mode !== "hours" && mode !== "manual";
}

export function draftOf(source?: Partial<KnowledgeSource>): RefreshDraft {
  const minutes = source?.interval_minutes ?? 60;
  const draft: RefreshDraft = {
    mode: minutes % 60 === 0 ? "hours" : "minutes",
    interval: String(minutes),
    hours: String(minutes % 60 === 0 ? minutes / 60 : 1),
    time: "06:00",
    days: [1],
    dayOfMonth: "1",
    cron: source?.refresh_cron ?? "",
    timezone: source?.refresh_timezone && source.refresh_timezone !== "UTC" ? source.refresh_timezone : browserTimezone(),
  };
  if (source?.refresh_mode === "manual") return { ...draft, mode: "manual" };
  if (source?.refresh_mode === "cron") {
    const preset = presetOf(source.refresh_cron ?? "");
    return { ...draft, ...(preset ?? { mode: "cron" }) };
  }
  return draft;
}

export type RefreshBody = {
  refresh_mode: "interval" | "cron" | "manual";
  interval_minutes?: number;
  refresh_cron?: string;
  refresh_timezone?: string;
};

/** The five cron fields of a choice made by time of day. */
function cronOf(draft: RefreshDraft): string {
  const [h, m] = (draft.time || "00:00").split(":").map((part) => Number.parseInt(part, 10));
  const minute = Number.isFinite(m) ? m : 0;
  const hour = Number.isFinite(h) ? h : 0;
  if (draft.mode === "daily") return `${minute} ${hour} * * *`;
  if (draft.mode === "weekdays") return `${minute} ${hour} * * 1-5`;
  if (draft.mode === "weekly") {
    const days = [...new Set(draft.days)].sort((a, b) => a - b);
    return `${minute} ${hour} * * ${days.length ? days.join(",") : "1"}`;
  }
  if (draft.mode === "monthly") return `${minute} ${hour} ${Math.max(1, Math.min(28, Number(draft.dayOfMonth) || 1))} * *`;
  return draft.cron.trim();
}

/** The body of a create or a patch: only the fields the mode uses. */
export function refreshBody(draft: RefreshDraft): RefreshBody {
  if (draft.mode === "manual") return { refresh_mode: "manual" };
  if (draft.mode === "minutes") return { refresh_mode: "interval", interval_minutes: Number(draft.interval) || 60 };
  if (draft.mode === "hours") return { refresh_mode: "interval", interval_minutes: (Number(draft.hours) || 1) * 60 };
  return { refresh_mode: "cron", refresh_cron: cronOf(draft), refresh_timezone: draft.timezone };
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
    ? "restricted: people read it through a grant; a run is served it only for a person who reads it"
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

/** One finished run of the history, in words: `failed (the model gateway failed) \u00b7 $1.20 \u00b7 by a@b.c`. */
export function runLabel(run: Record<string, unknown>): string {
  const status = String(run.status ?? "");
  const why = FAILURES[String(run.code ?? "")];
  const spent = Number(run.spent_usd ?? 0);
  const parts = Number(run.parts_total ?? 0) ? ` \u00b7 ${Number(run.parts_done ?? 0)}/${Number(run.parts_total)} parts` : "";
  return `${status}${why && status !== "pending_review" ? ` (${why})` : ""}${parts} \u00b7 $${spent.toFixed(2)}${run.requested_by ? ` \u00b7 by ${String(run.requested_by)}` : ""}`;
}
