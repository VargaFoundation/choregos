// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Alert, Button, Card, ErrorNote, ReadState } from "@/components/ui";
import { api } from "@/lib/api";
import type { ForgeInstance } from "@/lib/types";
import { instanceLabel, LINK_ERRORS, PROVIDER_LABELS } from "./forge";
import { LinkedAccountList } from "./linked-account-list";

/** Where the browser goes: a seam the tests replace. */
export const navigation = { assign: (url: string) => window.location.assign(url) };

/** What the callback said, from `?linked=` or `?link_error=` (ADR 0154). */
function outcomeOf(search: string): { ok: boolean; text: string } | null {
  const params = new URLSearchParams(search);
  const linked = params.get("linked");
  if (linked) return { ok: true, text: `Your ${PROVIDER_LABELS[linked as keyof typeof PROVIDER_LABELS] ?? linked} account is linked.` };
  const error = params.get("link_error");
  if (error) return { ok: false, text: LINK_ERRORS[error] ?? "The account was not linked." };
  return null;
}

/**
 * My accounts on code forges (ADR 0154): each one I proved by signing in to the forge, or an administrator
 * declared; a Link button per forge my organisations configured. The forge sends the browser back to the
 * API, which comes back here with the outcome.
 */
export function MyLinkedAccountsCard({ className, search }: { className?: string; search?: string }) {
  const queryClient = useQueryClient();
  const mine = useQuery({ queryKey: ["my-linked-accounts"], queryFn: () => api.myLinkedAccounts() });
  const [error, setError] = useState<string | null>(null);
  const [outcome] = useState(() => outcomeOf(search ?? (typeof window === "undefined" ? "" : window.location.search)));
  const items = mine.data?.items ?? [];
  const instances = mine.data?.instances ?? [];
  const multiOrg = new Set(instances.map((i) => i.org)).size > 1;

  async function link(instance: ForgeInstance) {
    setError(null);
    try {
      const started = await api.startMyLinkedAccount(instance.provider, instance.org, instance.instance);
      navigation.assign(started.authorization_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The link could not start");
    }
  }

  return (
    <Card title="Linked accounts" id="linked-accounts" className={className}>
      <p className="mb-3 text-xs text-ink-muted">
        Your accounts on code forges. Linking one signs you in to the forge once: the platform keeps its numeric id
        and login, never a token, and takes a signed comment from that account as yours.
      </p>
      {outcome && (
        <div className="mb-3">
          <Alert tone={outcome.ok ? "ok" : "warn"}>{outcome.text}</Alert>
        </div>
      )}
      <LinkedAccountList
        accounts={items}
        showOrg={multiOrg}
        label="Your linked accounts"
        onRemove={async (account) => {
          await api.deleteMyLinkedAccount(account.id);
          await queryClient.invalidateQueries({ queryKey: ["my-linked-accounts"] });
        }}
      />
      {mine.isSuccess && items.length === 0 && <p className="text-sm text-ink-muted">No account linked yet.</p>}
      {instances.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {instances.map((instance) => {
            const label = instanceLabel(instance.provider, instance.base_url);
            return (
              <Button key={`${instance.org}/${instance.instance}`} size="sm" onClick={() => void link(instance)}>
                {`Link ${label}${multiOrg ? ` (${instance.org})` : ""}`}
              </Button>
            );
          })}
        </div>
      )}
      {mine.isSuccess && instances.length === 0 && (
        <p className="mt-3 text-xs text-ink-muted">No forge is configured for linking yet: an administrator adds one under Connectors.</p>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
      <ReadState lecture={mine} quoi="your linked accounts" />
    </Card>
  );
}
