// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { RemoteCredentials } from "@/components/remote/credentials";
import { buttonClasses, Empty } from "@/components/ui";
import { useSession } from "@/lib/session";

/** The credentials remote agents are reached with (ADR 0062), and the way to register an agent. */
function RemoteAgentsAdmin() {
  const { org } = useSession();
  const connected = useSearchParams().get("connected");
  if (!org) return <Empty>Reading your session…</Empty>;
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-ink-muted">
        A remote agent is called by a workflow step over A2A and runs outside the platform, for example Atlassian&apos;s Rovo. Connect its
        credential here, then register the agent.
      </p>
      <Link href="/agents/new/remote" className={buttonClasses("primary")}>
        Register a remote agent
      </Link>
      <RemoteCredentials org={org} connected={connected} />
    </div>
  );
}

export default function RemoteAgentsAdminPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <RemoteAgentsAdmin />
    </Suspense>
  );
}
