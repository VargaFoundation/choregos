// SPDX-License-Identifier: Apache-2.0
"use client";

import { ConnectedAccounts } from "@/components/assistants/connected-accounts";
import { OrgPageHeader } from "@/components/org-page-header";

/** My connected accounts; the consent callback lands here with `?connected=` (S63-18). */
export default function ConnectedAccountsPage() {
  return (
    <div className="space-y-6">
      <OrgPageHeader title="Connected accounts" eyebrow="Assistants" />
      <ConnectedAccounts />
    </div>
  );
}
