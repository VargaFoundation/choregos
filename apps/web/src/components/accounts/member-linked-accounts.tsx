// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { DEFAULT_INSTANCES, PROVIDER_LABELS, type ForgeProvider } from "./forge";
import { LinkedAccountList } from "./linked-account-list";

const PROVIDERS = Object.keys(PROVIDER_LABELS) as ForgeProvider[];
const EMPTY = { provider: "github" as ForgeProvider, baseUrl: "", id: "", login: "" };

/**
 * A member's linked forge accounts, as an organisation administrator manages them (ADR 0154): the list with
 * its Verified and Declared badges, and the form that declares one by numeric id — stored NOT verified.
 */
export function MemberLinkedAccounts({ org, userId }: { org: string; userId: string }) {
  const client = useQueryClient();
  const key = ["member-linked-accounts", org, userId];
  const accounts = useQuery({ queryKey: key, queryFn: () => api.memberLinkedAccounts(org, userId) });
  const [draft, setDraft] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const needsInstance = !DEFAULT_INSTANCES[draft.provider];

  async function act(gesture: () => Promise<unknown>, reset = false) {
    setError(null);
    try {
      await gesture();
      if (reset) setDraft(EMPTY);
      await client.invalidateQueries({ queryKey: key });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }

  const declare = () =>
    act(
      () =>
        api.declareMemberLinkedAccount(org, userId, {
          provider: draft.provider,
          base_url: draft.baseUrl.trim() || null,
          external_id: draft.id,
          login: draft.login.trim(),
        }),
      true,
    );

  return (
    <div className="space-y-2 text-sm">
      <LinkedAccountList
        accounts={accounts.data ?? []}
        label="Linked accounts of this member"
        onRemove={(account) => act(() => api.deleteMemberLinkedAccount(org, userId, account.id))}
      />
      {accounts.isSuccess && (accounts.data ?? []).length === 0 && <p className="text-xs text-ink-muted">No linked account.</p>}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Forge</span>
          <Select
            value={draft.provider}
            onChange={(event) => setDraft({ ...draft, provider: event.target.value as ForgeProvider })}
            className="w-auto"
          >
            {PROVIDERS.map((provider) => (
              <option key={provider} value={provider}>
                {PROVIDER_LABELS[provider]}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Instance</span>
          <Input
            value={draft.baseUrl}
            placeholder={DEFAULT_INSTANCES[draft.provider] ?? "https://forge.example"}
            onChange={(event) => setDraft({ ...draft, baseUrl: event.target.value })}
            className="w-auto"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">Login</span>
          <Input value={draft.login} onChange={(event) => setDraft({ ...draft, login: event.target.value })} className="w-auto" />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">User id</span>
          <Input
            inputMode="numeric"
            value={draft.id}
            onChange={(event) => setDraft({ ...draft, id: event.target.value.replace(/\D/g, "") })}
            className="w-auto"
          />
        </label>
        <Button
          size="sm"
          tone="primary"
          disabled={!draft.login.trim() || !draft.id || (needsInstance && !draft.baseUrl.trim())}
          onClick={() => void declare()}
        >
          Declare
        </Button>
      </div>
      <p className="text-xs text-ink-muted">
        A declared account is shown as not verified: the member proves it by linking it from My account. The id is
        the forge&apos;s numeric user id, never the login.
      </p>
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
