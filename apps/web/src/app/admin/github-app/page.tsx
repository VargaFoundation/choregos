// SPDX-License-Identifier: Apache-2.0
"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { GitHubAppRegistration } from "@/components/admin/github-app-registration";
import { Lead } from "@/components/ui";

/**
 * The instance's GitHub App (S70-21, ADR 0161): which one the platform acts as, and, when the deployment
 * names none, its creation from a manifest. GitHub sends the browser back here with `?github_app=created`.
 */
export default function GitHubAppPage() {
  return (
    <div className="space-y-4">
      <Lead>
        One GitHub App serves this instance: the tracker, the pull requests and the run&apos;s git token go through it. Its
        owner on GitHub is whoever registers it.
      </Lead>
      <Suspense>
        <WithOutcome />
      </Suspense>
    </div>
  );
}

function WithOutcome() {
  return <GitHubAppRegistration outcome={useSearchParams().get("github_app")} />;
}
