// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { GesteConfirme } from "@/components/geste-confirme";
import { SettingsSection } from "@/components/project-settings/section";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";

/** Stopping a project, and retiring it. Each asks first, and says what it does. */
export function DangerZoneSection({ slug }: { slug: string }) {
  const client = useQueryClient();
  const project = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const status = project.data?.status;
  const refresh = () => client.invalidateQueries({ queryKey: ["project", slug] });
  return (
    <SettingsSection id="danger-zone" title="Danger zone" lead="Gestures that stop the project's work. Each one asks for a confirmation first.">
      <Card title="Suspend and archive">
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="min-w-0 flex-1">
              <strong>Suspend</strong> — no new run starts and nothing is deleted; the project stays readable.
              {status === "suspended" && <span className="text-warn"> It is suspended.</span>}
            </p>
            <GesteConfirme
              confirmer="Suspend the project"
              disabled={status !== "active"}
              question={`Suspend ${slug}? Runs in flight finish; no new one starts.`}
              action={() => api.suspendProject(slug)}
              onFait={() => void refresh()}
            >
              Suspend
            </GesteConfirme>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="min-w-0 flex-1">
              <strong>Archive</strong> — retires the project: it leaves the lists and its work stops. This is not undone from the console.
              {status === "archived" && <span className="text-warn"> It is archived.</span>}
            </p>
            <GesteConfirme
              confirmer="Archive the project"
              tonDuBouton="danger"
              disabled={status === "archived"}
              question={`Archive ${slug}? Its work stops and it leaves the project lists.`}
              action={() => api.deleteProject(slug)}
              onFait={() => void refresh()}
            >
              Archive
            </GesteConfirme>
          </div>
        </div>
      </Card>
    </SettingsSection>
  );
}
