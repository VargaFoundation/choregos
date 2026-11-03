// SPDX-License-Identifier: Apache-2.0
"use client";

import { LayoutGrid } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { SETTINGS_GROUPS } from "@/components/project-settings/groups";
import { cn } from "@/lib/cn";
import { HUE_CLASSES } from "@/lib/workflow-hue";

/**
 * The settings' groups (S55-07): each with its icon on its hue, the overview first. A strip on small
 * screens, a sticky column from `lg` up. It was a list of twelve anchors on one endless page, all in the
 * same grey.
 */
export function SettingsNav({ slug }: { slug: string }) {
  const pathname = usePathname();
  const base = `/p/${encodeURIComponent(slug)}/settings`;
  const links = [
    { href: base, label: "Overview", icon: LayoutGrid, tint: "border-line bg-surface-muted text-ink-muted" },
    ...SETTINGS_GROUPS.map((group) => ({
      href: `${base}/${group.id}`,
      label: group.label,
      icon: group.icon,
      tint: group.hue
        ? cn(HUE_CLASSES[group.hue].soft, HUE_CLASSES[group.hue].line, HUE_CLASSES[group.hue].ink)
        : "border-failed-line bg-failed-soft text-failed-ink",
    })),
  ];
  return (
    <nav aria-label="Settings groups" className="lg:sticky lg:top-4 lg:self-start">
      <ul className="flex gap-1 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0">
        {links.map(({ href, label, icon: Icon, tint }) => {
          const active = pathname === href;
          return (
            <li key={href} className="shrink-0">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm no-underline pointer-coarse:min-h-11",
                  active ? "bg-accent-soft font-medium text-ink" : "text-ink-muted hover:bg-surface-muted hover:text-ink",
                )}
              >
                <span aria-hidden className={cn("flex size-7 shrink-0 items-center justify-center rounded border", tint)}>
                  <Icon className="size-4" strokeWidth={1.75} />
                </span>
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
