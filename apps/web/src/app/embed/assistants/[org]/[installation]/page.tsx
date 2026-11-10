// SPDX-License-Identifier: Apache-2.0
"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { EmbeddedAssistant } from "@/components/embed/embedded-assistant";

/**
 * `/embed/assistants/{org}/{installation}?context=<base64url JSON>`: the assistant for a host application's
 * iframe (S63-12, ADR 0139 point 6). The proxy (`src/proxy.ts`) sets this route's `frame-ancestors` to the
 * installation's `config.embed_origins`; the root layout draws no chrome around it.
 */
export default function EmbedPage() {
  return (
    <Suspense>
      <Embedded />
    </Suspense>
  );
}

function Embedded() {
  const { org, installation } = useParams<{ org: string; installation: string }>();
  const params = useSearchParams();
  const memory = (params.get("memory") ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return (
    <EmbeddedAssistant
      org={org}
      installation={installation}
      rawContext={params.get("context")}
      signinRequired={params.get("signin") === "required"}
      memorySpaces={memory}
    />
  );
}
