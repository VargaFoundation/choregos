// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { use } from "react";
import { Empty } from "@/components/ui";
import { FlowScreen } from "@/components/workflows/flow/flow-screen";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { workflowHueClasses } from "@/lib/workflow-hue";

/**
 * The project's flow (S67-29): the default workflow's flow, with a switcher to the others. `?workflow=`
 * keeps the choice in the URL; `?wall=1` opens the wall mode.
 */
export default function ProjectFlowPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  const pathname = usePathname();
  const search = useSearchParams();
  const workflows = useQuery({ queryKey: ["workflows", slug], queryFn: () => api.workflows(slug) });
  const list = workflows.data ?? [];
  const fallback = list.find((w) => w.is_default) ?? list[0];
  const chosen = list.find((w) => w.name === search.get("workflow")) ?? fallback;
  if (workflows.isSuccess && !chosen) return <Empty>This project has no workflow yet.</Empty>;
  if (!chosen) return <p className="text-sm text-ink-muted">Reading the workflows.</p>;
  const pill = "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm no-underline whitespace-nowrap";
  return (
    <div className="space-y-4">
      <nav aria-label="Flow workflow" className="flex flex-wrap items-center gap-1.5">
        {list.map((workflow) => {
          const hue = workflowHueClasses(workflow.name);
          const active = workflow.name === chosen.name;
          return (
            <Link
              key={workflow.name}
              href={`${pathname}?workflow=${encodeURIComponent(workflow.name)}`}
              aria-current={active ? "page" : undefined}
              data-verbatim
              className={cn(pill, active ? cn(hue.pill, "font-medium") : "border-line text-ink-muted hover:text-ink")}
            >
              <span aria-hidden className={cn("size-2 rounded-full", hue.solid)} />
              <span>{workflow.name}</span>
              {workflow.is_default && <span className="text-xs">(default)</span>}
            </Link>
          );
        })}
      </nav>
      <FlowScreen
        key={chosen.name}
        slug={slug}
        workflow={chosen.name}
        isDefault={chosen.is_default}
        wallRequested={search.get("wall") === "1"}
      />
    </div>
  );
}
