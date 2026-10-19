// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Circle, CircleCheck, CircleDot } from "lucide-react";
import Link from "next/link";
import { Badge, Card, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ReadinessItem } from "@/lib/types";

/** The sections of a readiness list, in the order the settings page shows them. */
export const READINESS_SECTIONS: { id: ReadinessItem["section"]; label: string }[] = [
  { id: "general", label: "General" },
  { id: "connections", label: "Connections" },
  { id: "agents", label: "Agents & models" },
  { id: "guardrails", label: "Guardrails" },
  { id: "people", label: "People" },
  { id: "notifications", label: "Notifications" },
  { id: "tools", label: "Tools" },
];

const STATE = {
  done: { Icon: CircleCheck, tone: "text-ok", words: "done" },
  check: { Icon: CircleDot, tone: "text-warn", words: "to check" },
  todo: { Icon: Circle, tone: "text-danger", words: "to do" },
  not_needed: { Icon: CircleCheck, tone: "text-ink-muted", words: "not needed" },
} as const;

/** What a person who cannot fix an item is told to do. `null`: they can, the link is enough. */
export function askWho(by: ReadinessItem["fix"]["by"], viewer: { orgAdmin: boolean; projectOwner: boolean }): string | null {
  if (by === "project_owner") return viewer.projectOwner || viewer.orgAdmin ? null : "Ask a project owner";
  if (by === "org_admin") return viewer.orgAdmin ? null : "Ask your organisation admin";
  if (by === "platform_operator") return "Ask the platform operator";
  return "Done outside Choregos";
}

/**
 * A fix link, made local to the settings page: the API names a console path with the anchor of the
 * section (`/p/dev/settings#agents`), and on this page that is a plain `#anchor` (the agents section is
 * `agents-models` in the DOM). Links to other pages (Members, Workflows) stay as they are.
 */
export function localHref(href: string, slug: string): string {
  const settings = `/p/${slug}/settings`;
  if (href !== settings && !href.startsWith(`${settings}#`)) return href;
  const anchor = href.slice(settings.length + 1) || "general";
  return `#${anchor === "agents" ? "agents-models" : anchor}`;
}

/** The items to show (the ones the project's workflows do not need are left out), grouped by section. */
export function groupReadiness(items: ReadinessItem[]): { id: ReadinessItem["section"]; label: string; items: ReadinessItem[] }[] {
  return READINESS_SECTIONS.map((section) => ({
    ...section,
    items: items.filter((item) => item.section === section.id && item.state !== "not_needed"),
  })).filter((group) => group.items.length > 0);
}

/** What the project still lacks to run, as the API computes it, with the link that fixes each item. */
export function ReadinessPanel({ slug }: { slug: string }) {
  const { me, org } = useSession();
  const readiness = useQuery({ queryKey: ["readiness", slug], queryFn: () => api.projectReadiness(slug) });
  if (readiness.error) return <ErrorNote>{String(readiness.error)}</ErrorNote>;
  const data = readiness.data;
  if (!data) return null;
  const memberships = (me?.memberships ?? []).filter((m) => m.org === org);
  const viewer = {
    orgAdmin: memberships.some((m) => !m.project_slug && m.role === "org_admin"),
    projectOwner: memberships.some((m) => m.project_slug === slug && m.role === "project_owner"),
  };
  const { done, check, todo } = data.summary;
  const groups = groupReadiness(data.items);
  return (
    <Card
      title="Readiness"
      action={
        <span className="flex items-center gap-2 text-xs text-ink-muted">
          {data.ready ? (
            <Badge tone="ok">Ready to run</Badge>
          ) : (
            <Badge tone="warn">
              {todo} to do
            </Badge>
          )}
          <span data-testid="readiness-summary">
            {done} of {done + check + todo} ready{check > 0 ? `, ${check} to check` : ""}
          </span>
        </span>
      }
      data-testid="readiness-panel"
    >
      <div className="space-y-4">
        {groups.map((group) => (
          <section key={group.id} aria-label={`${group.label}, readiness`} data-testid={`readiness-${group.id}`}>
            <h3 className="mb-1 text-xs font-semibold text-ink-muted">{group.label}</h3>
            <ul className="divide-y divide-line">
              {group.items.map((item) => {
                const { Icon, tone, words } = STATE[item.state];
                const who = item.state === "done" ? null : askWho(item.fix.by, viewer);
                return (
                  <li key={item.id} className="flex items-start gap-3 py-2 text-sm" data-testid={`readiness-item-${item.id}`} data-state={item.state}>
                    <Icon aria-hidden className={`mt-0.5 size-4 shrink-0 ${tone}`} />
                    <span className="min-w-0 flex-1">
                      <span className={item.state === "done" ? "text-ink-muted" : "font-medium"}>{item.title}</span>
                      <span className="sr-only">{` (${words})`}</span>
                      <span className="block text-xs text-ink-muted">{item.detail}</span>
                    </span>
                    {item.state !== "done" && (
                      <span className="shrink-0 text-right text-xs">
                        {who ? (
                          <span className="text-ink-muted">{who}</span>
                        ) : (
                          item.fix.href && (
                            <Link href={localHref(item.fix.href, slug)} data-testid={`fix-${item.id}`}>
                              Fix
                            </Link>
                          )
                        )}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </Card>
  );
}
