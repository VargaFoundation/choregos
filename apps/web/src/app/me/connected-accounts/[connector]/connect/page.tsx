// SPDX-License-Identifier: Apache-2.0
"use client";

import { useSearchParams } from "next/navigation";
import { use } from "react";
import { ConnectAccountRedirect } from "@/components/assistants/connected-accounts";

/** What `account_not_connected` points to: starts the consent of the person at one source (S63-18). */
export default function ConnectAccountPage({ params }: { params: Promise<{ connector: string }> }) {
  const { connector } = use(params);
  const org = useSearchParams().get("org") ?? "";
  return <ConnectAccountRedirect connector={connector} org={org} />;
}
