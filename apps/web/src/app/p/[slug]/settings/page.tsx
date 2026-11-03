// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect } from "react";
import { groupOfAnchor, settingsHref, SETTINGS_GROUPS } from "@/components/project-settings/groups";
import { ReadinessPanel } from "@/components/project-settings/readiness-panel";
import { cn } from "@/lib/cn";
import { HUE_CLASSES } from "@/lib/workflow-hue";

/**
 * A project's settings, their first page (S55-07): what the project still lacks to run, then its groups
 * of settings as cards — each with its icon, its hue, what it is for and what it holds. A link to an
 * old anchor (`/settings#tools`, the API's `#agents`) lands on the group that holds it.
 */
export default function SettingsOverviewPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const router = useRouter();
  useEffect(() => {
    const anchor = window.location.hash.slice(1);
    if (anchor && groupOfAnchor(anchor)) router.replace(settingsHref(slug, anchor));
  }, [router, slug]);
  return (
    <>
      <section id="readiness" aria-label="Readiness" className="scroll-mt-4 empty:hidden">
        <ReadinessPanel slug={slug} />
      </section>
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label="Settings groups overview">
        {SETTINGS_GROUPS.map((group) => {
          const Icon = group.icon;
          const hue = group.hue ? HUE_CLASSES[group.hue] : undefined;
          return (
            <li key={group.id}>
              <Link
                href={`/p/${encodeURIComponent(slug)}/settings/${group.id}`}
                data-testid={`settings-group-${group.id}`}
                className={cn(
                  "raised flex h-full gap-3 rounded-md border border-line border-t-4 p-4 no-underline transition-colors hover:border-line-strong",
                  hue ? hue.stripe : "border-t-failed",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "flex size-9 shrink-0 items-center justify-center rounded-md border",
                    hue ? cn(hue.soft, hue.line, hue.ink) : "border-failed-line bg-failed-soft text-failed-ink",
                  )}
                >
                  <Icon className="size-5" strokeWidth={1.75} />
                </span>
                <span className="min-w-0 space-y-1">
                  <span className="block font-medium text-ink">{group.label}</span>
                  <span className="block text-sm text-ink-muted">{group.lead}</span>
                  {group.sections.length > 1 && (
                    <span className="block text-xs text-ink-muted">
                      {group.sections.map((section) => section.label).join(" · ")}
                    </span>
                  )}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </>
  );
}
