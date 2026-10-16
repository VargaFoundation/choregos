// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { Circle, CircleCheck, CircleDot } from "lucide-react";
import Link from "next/link";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";

const ICON = {
  done: { Icon: CircleCheck, tone: "text-ok", words: "done" },
  check: { Icon: CircleDot, tone: "text-warn", words: "to check" },
  todo: { Icon: Circle, tone: "text-danger", words: "to do" },
} as const;

/**
 * The getting-started list at the top of a project's overview. It is the project's readiness, computed by
 * the API (`GET /projects/{id}/readiness`), so this card and the settings page say the same thing; it
 * disappears once nothing is left to do. (Before, the console worked it out itself from four reads, and
 * its answer had drifted from the truth: a required CD with no connector read as done.)
 */
export function MiseEnRoute({ slug }: { slug: string }) {
  const readiness = useQuery({ queryKey: ["readiness", slug], queryFn: () => api.projectReadiness(slug) });
  if (!readiness.data || readiness.data.ready) return null;
  const items = readiness.data.items.filter((item) => item.state !== "not_needed");
  const { done } = readiness.data.summary;
  return (
    <Card
      title="Getting started"
      action={
        <span className="text-xs text-ink-muted">
          {done} of {items.length} done
        </span>
      }
    >
      <ol className="divide-y divide-line" data-testid="mise-en-route">
        {items.map((item) => {
          const { Icon, tone, words } = ICON[item.state as keyof typeof ICON];
          return (
            <li key={item.id} className="flex items-start gap-3 py-2.5 text-sm" data-testid={`etape-${item.id}`} data-state={item.state}>
              <Icon aria-hidden className={`mt-0.5 size-4 shrink-0 ${tone}`} />
              <span className="min-w-0 flex-1">
                <span className={item.state === "done" ? "text-ink-muted" : "font-medium"}>{item.title}</span>
                <span className="sr-only">{` (${words})`}</span>
                <span className="block text-xs text-ink-muted">{item.detail}</span>
              </span>
              {item.state !== "done" && item.fix.href && (
                <Link href={item.fix.href} className="shrink-0 text-xs">
                  Fix
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
