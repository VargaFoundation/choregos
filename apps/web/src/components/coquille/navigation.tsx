// SPDX-License-Identifier: Apache-2.0
"use client";

import {
  ArrowLeft,
  BookOpen,
  Bot,
  Brain,
  Cpu,
  ChevronsUpDown,
  FolderKanban,
  Inbox,
  Library,
  LayoutDashboard,
  Plug,
  ScrollText,
  Server,
  Settings,
  SquareKanban,
  TrainFront,
  TriangleAlert,
  Users,
  Wallet,
  Workflow,
  Zap,
  BadgeCheck,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { hasReleaseTrain } from "@/lib/release-train";
import { cn } from "@/lib/cn";
import type { BreadcrumbStep } from "@/components/ui";
import { useSession } from "@/lib/session";

// Skills have their own entry (review of 7 Oct: "there is no direct link in the menu"). The
// `/integrations` page is where a person connects their AI assistant to Choregos through the MCP
// door (inbound, whereas the connectors in the administration are where Choregos reaches out). It
// bears the name of the project's own entry, "Integrations", and sits in the Organisation group:
// it is how the organisation's people plug in, not something agents use (S40-08).
export const NAV: { href: string; label: string; icone: LucideIcon }[] = [
  { href: "/", label: "Projects", icone: FolderKanban },
  { href: "/agents", label: "Agents", icone: Bot },
  { href: "/skills", label: "Skills", icone: BookOpen },
  { href: "/knowledge", label: "Knowledge", icone: Library },
  { href: "/memories", label: "Memories", icone: Brain },
  { href: "/inbox", label: "Inbox", icone: Inbox },
  { href: "/integrations", label: "Integrations", icone: Plug },
  { href: "/costs", label: "Spend", icone: Wallet },
  { href: "/admin", label: "Admin", icone: Settings },
];

export function entreeActive(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" || pathname.startsWith("/p/") : pathname.startsWith(href);
}

/** The entries, grouped: the work, what agents use, the organisation (S40-08). */
export const GROUPES: { titre: string; entrees: string[] }[] = [
  { titre: "Work", entrees: ["/", "/inbox"] },
  { titre: "Catalogue", entrees: ["/agents", "/skills", "/knowledge", "/memories"] },
  { titre: "Organisation", entrees: ["/integrations", "/costs", "/admin"] },
];

// ───────────────────────────── scopes (S30-03) ─────────────────────────────

/**
 * Where the navigation stands (S30-03, plan item 11): the sidebar switches scope, Temporal-style.
 * At the organisation's level it lists the organisation's pages; inside a project, the project's
 * sections; inside the administration, its sections. The tab rows of the project and of the
 * administration are gone: this list and the breadcrumbs carry the hierarchy.
 */
export type Scope = { kind: "org" } | { kind: "project"; slug: string } | { kind: "admin" };

export function scopeOf(pathname: string): Scope {
  const project = /^\/p\/([^/]+)/.exec(pathname);
  if (project) return { kind: "project", slug: decodeURIComponent(project[1]!) };
  if (pathname === "/admin" || pathname.startsWith("/admin/")) return { kind: "admin" };
  return { kind: "org" };
}

export interface NavEntry {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Counts what waits for a person (the inbox). */
  counts?: boolean;
  active: (pathname: string) => boolean;
}

export interface NavGroup {
  title: string;
  entries: NavEntry[];
}

/** What a project shows: a section can depend on what its workflows declare (S30-07 sets `trains`). */
export interface ProjectFeatures {
  trains?: boolean;
}

export function orgGroups(): NavGroup[] {
  return GROUPES.map((groupe) => ({
    title: groupe.titre,
    entries: groupe.entrees.map((href) => {
      const entree = NAV.find((n) => n.href === href)!;
      return {
        href,
        label: entree.label,
        icon: entree.icone,
        counts: href === "/inbox",
        active: (pathname: string) => entreeActive(href, pathname),
      };
    }),
  }));
}

/**
 * A project's sections, grouped: Work, Define, Configure (plan item 11). Only routes that exist
 * are linked: costs have no page of their own yet — they are on the overview, which leads the list.
 */
export function projectGroups(slug: string, features: ProjectFeatures = {}): NavGroup[] {
  const base = `/p/${encodeURIComponent(slug)}`;
  const section = (suffix: string, label: string, icon: LucideIcon, also: string[] = []): NavEntry => ({
    href: `${base}${suffix}`,
    label,
    icon,
    active: (pathname: string) =>
      suffix === ""
        ? pathname === base
        : [suffix, ...also].some((s) => pathname === `${base}${s}` || pathname.startsWith(`${base}${s}/`)),
  });
  const trains = features.trains ?? true;
  return [
    {
      title: "Work",
      entries: [
        section("", "Overview", LayoutDashboard),
        // A ticket and its runs belong to the board (S23-12).
        section("/board", "Board", SquareKanban, ["/items", "/runs"]),
        { ...section("/inbox", "Inbox", Inbox), counts: true },
        ...(trains ? [section("/trains", "Trains", TrainFront)] : []),
        section("/findings", "Findings", TriangleAlert),
      ],
    },
    {
      title: "Define",
      entries: [
        section("/workflows", "Workflows", Workflow, ["/workflow"]),
        section("/agents", "Agents", Bot),
        section("/knowledge", "Knowledge", Library),
        section("/actions", "Actions", Zap),
        section("/memory", "Memory", Brain),
      ],
    },
    {
      title: "Configure",
      entries: [
        section("/integrations", "Integrations", Plug),
        section("/members", "Members", Users),
        section("/settings", "Settings", Settings),
      ],
    },
  ];
}

/** The administration's sections: the core's screens, then one per section a plugin declares (ADR 0032). */
export function adminGroups(pluginSections: { id: string; title: string }[] = []): NavGroup[] {
  const page = (href: string, label: string, icon: LucideIcon): NavEntry => ({
    href,
    label,
    icon,
    active: (pathname: string) => pathname === href,
  });
  return [
    {
      title: "Administration",
      entries: [
        page("/admin", "Overview", LayoutDashboard),
        page("/admin/members", "Members", Users),
        page("/admin/connectors", "Connectors", Plug),
        page("/admin/models", "Models", Cpu),
        page("/admin/remote-agents", "Remote agents", Bot),
        page("/admin/audit", "Audit", ScrollText),
        page("/admin/platform", "Platform", Server),
        page("/admin/edition", "Edition", BadgeCheck),
        ...pluginSections.map((s) => page(`/admin/x/${s.id}`, s.title, Settings)),
      ],
    },
  ];
}

/** The label of the section a path is in, for the breadcrumbs. */
export function sectionOf(pathname: string, groups: NavGroup[]): NavEntry | undefined {
  return groups.flatMap((g) => g.entries).find((e) => e.href !== "/inbox" && e.active(pathname));
}

/**
 * The steps below a project's section, from its path: a workflow, an action, a client — an
 * identifier, shown as written. A ticket and a run write their own trail (they know its key).
 */
export function projectTrail(slug: string, pathname: string): BreadcrumbStep[] | null {
  const base = `/p/${encodeURIComponent(slug)}`;
  if (/\/(items|runs)\//.test(pathname.slice(base.length))) return null;
  const section = sectionOf(pathname, projectGroups(slug));
  if (!section || section.href === base) return [];
  const reste = pathname.slice(section.href.length).split("/").filter(Boolean);
  if (reste.length === 0) return [{ label: section.label }];
  return [
    { href: section.href, label: section.label },
    { label: <span data-donnee>{decodeURIComponent(reste[0]!)}</span> },
  ];
}

/** The groups of the current scope; the queries are those the pages read too (one request each). */
export function useNavGroups(pathname: string, features?: ProjectFeatures): { scope: Scope; groups: NavGroup[] } {
  const scope = scopeOf(pathname);
  const { org } = useSession();
  const sections = useQuery({
    queryKey: ["admin-sections", org],
    queryFn: () => api.adminSections(org),
    retry: false,
    enabled: scope.kind === "admin",
  });
  // Trains only exist when a workflow of the project declares a release train (S30-07): same query as
  // the project pages, so one request. Until it is read the section stays, so the sidebar does not jump.
  const project = useQuery({
    queryKey: ["project", scope.kind === "project" ? scope.slug : ""],
    queryFn: () => api.project(scope.kind === "project" ? scope.slug : ""),
    enabled: scope.kind === "project",
  });
  const groups =
    scope.kind === "project"
      ? projectGroups(scope.slug, { trains: project.data === undefined || hasReleaseTrain(project.data), ...features })
      : scope.kind === "admin"
        ? adminGroups(sections.data ?? [])
        : orgGroups();
  return { scope, groups };
}

// ───────────────────────────── rendering ─────────────────────────────

/** Le nombre de choses qui attendent quelqu'un : lu à voix haute comme tel, pas comme un chiffre nu. */
export function Compte({ n, className }: { n: number; className?: string }) {
  return (
    <span
      className={cn(
        "ml-1.5 inline-flex min-w-5 items-center justify-center rounded-full bg-accent px-1.5 font-mono text-[11px] tabular-nums text-accent-ink",
        className,
      )}
    >
      <span aria-hidden>{n}</span>
      <span className="sr-only">, {n} waiting</span>
    </span>
  );
}

/**
 * The head of a scope: inside a project, the way back to the organisation and the project switcher;
 * inside the administration, the way back. A `<details>` menu of links: no script, a keyboard
 * opens it with Enter.
 */
function ScopeHeader({ scope, compact, rail }: { scope: Scope; compact: boolean; rail: boolean }) {
  const { org } = useSession();
  const projects = useQuery({
    queryKey: ["projects", org],
    queryFn: () => api.projects(org),
    enabled: scope.kind === "project",
  });
  const project = useQuery({
    queryKey: ["project", scope.kind === "project" ? scope.slug : ""],
    queryFn: () => api.project(scope.kind === "project" ? scope.slug : ""),
    enabled: scope.kind === "project",
  });
  if (scope.kind === "org") return null;
  const back = (
    <Link
      href="/"
      title={rail ? `All of ${org}` : undefined}
      className={cn(
        "flex items-center gap-2 rounded px-2 text-xs text-ink-muted no-underline hover:bg-surface-muted hover:text-ink",
        compact ? "h-7 pointer-coarse:min-h-11 rail:justify-center rail:px-0" : "min-h-11",
      )}
    >
      <ArrowLeft aria-hidden className="size-4 shrink-0" strokeWidth={1.75} />
      <span className={cn(compact && "rail:sr-only")}>
        All of <span data-donnee className="font-mono">{org}</span>
      </span>
    </Link>
  );
  if (scope.kind === "admin") return <div className="mb-3">{back}</div>;
  const name = project.data?.name ?? scope.slug;
  return (
    <div className="mb-3 space-y-1" data-testid="project-switcher">
      {back}
      <details className={cn("group relative", compact && "rail:hidden")}>
        <summary
          className="flex cursor-pointer list-none items-center gap-2 rounded border border-line px-2 py-1.5 text-sm font-medium text-ink hover:bg-surface-muted [&::-webkit-details-marker]:hidden"
          aria-label={`Project ${name}: switch project`}
        >
          <FolderKanban aria-hidden className="size-4 shrink-0 text-ink-muted" strokeWidth={1.75} />
          <span data-donnee className="min-w-0 flex-1 truncate">
            {name}
          </span>
          <ChevronsUpDown aria-hidden className="size-4 shrink-0 text-ink-muted" strokeWidth={1.75} />
        </summary>
        <ul
          aria-label="Switch project"
          className="raised absolute inset-x-0 z-30 mt-1 max-h-72 overflow-y-auto rounded-md border border-line p-1 shadow-raised"
        >
          {(projects.data?.items ?? []).map((p) => (
            <li key={p.slug}>
              <Link
                href={`/p/${p.slug}`}
                aria-current={p.slug === scope.slug ? "true" : undefined}
                className={cn(
                  "flex min-h-8 items-center rounded px-2 text-sm no-underline hover:bg-surface-muted",
                  p.slug === scope.slug ? "font-medium text-ink" : "text-ink-muted",
                )}
              >
                <span data-donnee>{p.name ?? p.slug}</span>
              </Link>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

/**
 * The navigation in groups, the same everywhere: in the sidebar (dense, folds into a rail) and in the
 * menu panel below 1280 px (44 px targets). It follows the scope: the organisation's, a project's or
 * the administration's (S30-03).
 *
 * Both can be in the page at once — the bar is only hidden below 1280 px —: the captions' ids come
 * from `useId`, or `aria-labelledby` would point at a duplicate.
 */
export function ListeDeNavigation({
  pathname,
  aDecider,
  variante,
  rail = false,
  features,
}: {
  pathname: string;
  /** Ce qui attend une personne, compté sur l'entrée « inbox » (S23-02). */
  aDecider: number;
  variante: "barre" | "panneau";
  /** La barre est repliée : les icônes portent leur libellé en infobulle. */
  rail?: boolean;
  /** What the current project shows (S30-07 hides the trains of a project without one). */
  features?: ProjectFeatures;
}) {
  const id = useId();
  const barre = variante === "barre";
  const { scope, groups } = useNavGroups(pathname, features);
  return (
    <>
      <ScopeHeader scope={scope} compact={barre} rail={rail} />
      {groups.map((groupe) => {
        const idTitre = `${id}-${groupe.title}`;
        return (
          <div key={groupe.title} className={barre ? "mb-3" : "mt-4"}>
            {/* A caption, not a heading: the page carries its only h1 and its sections. */}
            <p id={idTitre} className={cn("px-2 pb-1 text-xs font-medium text-ink-muted", barre && "rail:sr-only")}>
              {groupe.title}
            </p>
            <ul aria-labelledby={idTitre}>
              {groupe.entries.map((entree) => {
                const Icone = entree.icon;
                const active = entree.active(pathname);
                return (
                  <li key={entree.href}>
                    <Link
                      href={entree.href}
                      aria-current={active ? "page" : undefined}
                      title={barre && rail ? entree.label : undefined}
                      className={cn(
                        "relative flex items-center rounded no-underline",
                        barre
                          ? "h-7 gap-2.5 px-2 text-[13px] pointer-coarse:min-h-11 rail:justify-center rail:px-0"
                          : "min-h-11 gap-3 px-3 text-sm",
                        active ? "bg-accent-soft font-medium text-ink" : "text-ink-muted hover:bg-surface-muted hover:text-ink",
                      )}
                    >
                      <Icone aria-hidden className="size-4 shrink-0" strokeWidth={1.75} />
                      <span className={cn(barre && "rail:sr-only")}>{entree.label}</span>
                      {entree.counts && aDecider > 0 && (
                        <Compte
                          n={aDecider}
                          className={cn(
                            "ml-auto",
                            barre && "rail:absolute rail:top-0 rail:right-0.5 rail:min-w-4 rail:px-1 rail:text-[10px]",
                          )}
                        />
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </>
  );
}
