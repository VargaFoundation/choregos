// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ActivityView } from "@/components/activity/activity-view";
import { OrgPageHeader } from "@/components/org-page-header";
import { Empty, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/**
 * What is running in the organisation, and what has just finished (S64-06, ADR 0142 §1). The whole
 * organisation by default; `?project=<slug>` narrows it to one project.
 */
function Activity() {
  const { org } = useSession();
  const router = useRouter();
  const project = useSearchParams().get("project");
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org) });
  return (
    <div className="space-y-6">
      <OrgPageHeader
        title="Activity"
        eyebrow="What is running"
        description="Runs, agent sessions and conversation turns in flight, then those that ended in the last hour, with what each has spent so far. A run can be stopped here, with a reason."
      />
      <label className="block space-y-1 text-xs text-ink-muted">
        <span>Scope</span>
        <Select
          aria-label="Scope"
          className="w-auto"
          value={project ?? ""}
          onChange={(e) => router.replace(e.target.value ? `/activity?project=${encodeURIComponent(e.target.value)}` : "/activity")}
        >
          <option value="">Whole organisation</option>
          {(projects.data?.items ?? []).map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.slug}
            </option>
          ))}
        </Select>
      </label>
      <ActivityView key={project ?? ""} org={org} project={project} />
    </div>
  );
}

export default function ActivityPage() {
  return (
    <Suspense fallback={<Empty>Reading what is running…</Empty>}>
      <Activity />
    </Suspense>
  );
}
