// SPDX-License-Identifier: Apache-2.0
"use client";

import { ConfirmedGesture } from "@/components/confirmed-gesture";
import { Badge } from "@/components/ui";
import { shortDate } from "@/lib/format";
import type { LinkedAccount } from "@/lib/types";
import { instanceLabel } from "./forge";

/**
 * A list of linked forge accounts (ADR 0154): the forge, the login and its profile, and whether the member
 * proved it (Verified) or an administrator declared it (Declared). The console draws the avatar from the
 * login: it loads no image from a forge. Reused by My account, the members' administration and, later, a
 * member's profile.
 */
export function LinkedAccountList({
  accounts,
  onRemove,
  showOrg = false,
  label = "Linked accounts",
}: {
  accounts: LinkedAccount[];
  /** Offered when given: removes the link after a confirmation. */
  onRemove?: (account: LinkedAccount) => Promise<unknown>;
  showOrg?: boolean;
  label?: string;
}) {
  if (accounts.length === 0) return null;
  return (
    <ul aria-label={label} className="divide-y divide-line">
      {accounts.map((account) => (
        <LinkedAccountRow key={account.id} account={account} onRemove={onRemove} showOrg={showOrg} />
      ))}
    </ul>
  );
}

function LinkedAccountRow({
  account,
  onRemove,
  showOrg,
}: {
  account: LinkedAccount;
  onRemove?: (account: LinkedAccount) => Promise<unknown>;
  showOrg: boolean;
}) {
  const forge = instanceLabel(account.provider, account.base_url);
  const facts = [
    showOrg ? account.org : null,
    `ID ${account.external_id}`,
    account.verified && account.verified_at
      ? `verified ${shortDate(account.verified_at)}`
      : account.created_by
        ? `declared by ${account.created_by}`
        : "declared",
  ].filter(Boolean);
  return (
    <li data-testid={`linked-${account.provider}-${account.external_id}`} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
      <span
        aria-hidden
        className="flex size-8 shrink-0 items-center justify-center rounded-full border border-line bg-surface-muted text-xs font-medium text-ink"
      >
        {account.login.slice(0, 1).toUpperCase()}
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-sm text-ink">
          <span className="text-ink-muted">{forge}</span>
          {account.profile_url ? (
            <a href={account.profile_url} target="_blank" rel="noreferrer" className="font-medium" data-verbatim>
              {account.login}
            </a>
          ) : (
            <span className="font-medium" data-verbatim>
              {account.login}
            </span>
          )}
          {account.verified ? (
            <Badge tone="ok" variant="soft" title="The member signed in to the forge to link it">
              Verified
            </Badge>
          ) : (
            <Badge tone="warn" variant="soft" title="An administrator declared it: the member has not proved it">
              Declared
            </Badge>
          )}
        </p>
        <p className="text-xs text-ink-muted">{facts.join(" · ")}</p>
      </div>
      {onRemove && (
        <ConfirmedGesture
          tonDuBouton="danger"
          question={`Unlink ${account.login} on ${forge}? Comments signed by this account are no longer taken as this member's.`}
          confirmer="Unlink"
          action={async () => {
            await onRemove(account);
          }}
        >
          Unlink
        </ConfirmedGesture>
      )}
    </li>
  );
}
