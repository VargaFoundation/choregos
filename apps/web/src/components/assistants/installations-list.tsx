// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { InstallAssistant } from "@/components/assistants/install-assistant";
import { OrgPageHeader } from "@/components/org-page-header";
import { Badge, Card, Empty, ReadState, buttonClasses } from "@/components/ui";
import { assistantsApi } from "@/lib/assistants-api";
import { useSession } from "@/lib/session";

const STATUS_TONE = { ready: "succeeded", incomplete: "waiting", suspended: "neutral" } as const;

/** The installations an owner manages, and (administrators) the form to install another. */
export function InstallationsList() {
  const { org, me } = useSession();
  const router = useRouter();
  const installations = useQuery({ queryKey: ["installations", org], queryFn: () => assistantsApi.installations(org) });
  const admin = (me?.memberships ?? []).some((m) => m.org === org && !m.project_slug && m.role === "org_admin");
  const items = installations.data?.items ?? [];
  return (
    <div className="space-y-6">
      <OrgPageHeader
        title="Assistant installations"
        eyebrow="Owners"
        description="Configure the assistants you own: bindings, knowledge, budgets, audit."
        actions={
          <Link href="/assistants" className={buttonClasses("secondary")}>
            Assistants
          </Link>
        }
      />
      <ReadState lecture={installations} quoi="the installations" />
      {installations.data && items.length === 0 && (
        <Empty title="No installation">Nothing is installed that you manage.</Empty>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {items.map((a) => (
          <Card key={a.slug} title={a.display_name ?? a.assistant_name} verbatimTitle interactive>
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="text-ink-muted">
                {a.plugin} {a.plugin_version} · {a.channel}
              </span>
              <Badge tone={STATUS_TONE[a.status]}>{a.status}</Badge>
            </div>
            <Link href={`/assistants/manage/${encodeURIComponent(a.slug)}`} className={buttonClasses("secondary", "sm", "mt-3")}>
              Open
            </Link>
          </Card>
        ))}
      </div>
      {admin && <InstallAssistant org={org} onInstalled={(slug) => router.push(`/assistants/manage/${encodeURIComponent(slug)}`)} />}
    </div>
  );
}
