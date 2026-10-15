// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { InboxView } from "@/components/inbox-view";
import { Select } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/** The organisation's inbox (S23-02), narrowable to one project with `?project=` (S39-04). */
export default function InboxPage() {
  const { org } = useSession();
  const router = useRouter();
  const project = useSearchParams().get("project") ?? undefined;
  const projects = useQuery({ queryKey: ["projects", org], queryFn: () => api.projects(org), staleTime: 30_000 });
  return (
    <div className="space-y-6">
      <label className="flex items-center gap-2 text-sm">
        <span className="text-ink-muted">Project</span>
        <Select
          aria-label="Project"
          value={project ?? ""}
          onChange={(event) => router.replace(event.target.value ? `/inbox?project=${encodeURIComponent(event.target.value)}` : "/inbox")}
        >
          <option value="">All projects</option>
          {(projects.data?.items ?? []).map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.name}
            </option>
          ))}
        </Select>
      </label>
      <InboxView key={project ?? "all"} project={project} />
    </div>
  );
}
