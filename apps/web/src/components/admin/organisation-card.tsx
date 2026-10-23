// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, ErrorNote, Input, Select } from "@/components/ui";
import { GesteConfirme } from "@/components/geste-confirme";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

const SLUG = /^[a-z0-9][a-z0-9-]*$/;

/**
 * The organisation itself (S40-24): its display name and cost currency, which its administrator
 * changes, and its slug, which only a platform administrator changes — behind a confirmation that
 * lists what the change breaks. Global memories follow the slug (they live under a key pinned at the
 * first change), so they are not on that list.
 */
export function OrganisationCard({ org, canEdit }: { org: string; canEdit: boolean }) {
  const { orgs, choisirOrg } = useSession();
  const client = useQueryClient();
  const current = (orgs ?? []).find((o) => o.slug === org);
  // The same read the sign-in card does: only a platform administrator is answered (403 otherwise).
  const platform = useQuery({ queryKey: ["platform-identity"], queryFn: () => api.platformIdentity(), retry: false });
  const [name, setName] = useState<string | null>(null);
  const [currency, setCurrency] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const shownName = name ?? current?.name ?? "";
  const shownCurrency = currency ?? current?.currency ?? "EUR";

  async function save() {
    setError(null);
    setSaved(false);
    try {
      await api.updateOrg(org, { name: shownName.trim(), currency: shownCurrency as "EUR" | "USD" });
      await client.invalidateQueries({ queryKey: ["orgs"] });
      setName(null);
      setCurrency(null);
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "refused");
    }
  }

  async function changeSlug(typed: string) {
    if (!SLUG.test(typed)) throw new Error("A slug is lowercase letters, digits and dashes, and starts with a letter or digit.");
    if (typed === org) throw new Error("That is already the slug.");
    await api.updateOrg(org, { slug: typed });
    // The console follows the organisation: remember the new slug, then reload on /admin so nothing
    // keyed by the old one survives.
    choisirOrg(typed);
    window.location.assign(new URL("/admin", window.location.origin).toString());
  }

  const valid = shownName.trim().length >= 1 && shownName.trim().length <= 200;
  return (
    <Card title="Organisation" className="lg:col-span-2">
      <form
        className="flex flex-wrap items-end gap-3 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          if (canEdit && valid) void save();
        }}
      >
        <label className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="text-xs text-ink-muted">Display name</span>
          <Input
            value={shownName}
            maxLength={200}
            disabled={!canEdit}
            onChange={(event) => setName(event.target.value)}
            className="min-w-0"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Currency</span>
          <Select value={shownCurrency} disabled={!canEdit} onChange={(event) => setCurrency(event.target.value)}>
            <option value="EUR">EUR</option>
            <option value="USD">USD</option>
          </Select>
        </label>
        {canEdit && (
          <Button size="sm" tone="primary" type="submit" disabled={!valid}>
            Save the organisation
          </Button>
        )}
      </form>
      {saved && <p className="mt-2 text-xs text-ink-muted">Organisation saved</p>}
      {error && <ErrorNote>{error}</ErrorNote>}
      <p className="mt-3 text-xs text-ink-muted">
        Slug <code className="font-mono" data-donnee>{org}</code> — used in every URL, MCP address and identity-provider group.
      </p>
      {platform.isSuccess && (
        <div className="mt-2">
          <GesteConfirme
            ton="primary"
            question={
              <>
                Change the slug of <code className="font-mono">{org}</code>? Everything that names it by slug stops working until
                it is updated: console URLs already copied, MCP addresses{" "}
                <code className="font-mono">/mcp/projects/{org}:&lt;project&gt;</code>, A2A addresses{" "}
                <code className="font-mono">/a2a/orgs/{org}</code>, identity-provider groups{" "}
                <code className="font-mono">choregos:{org}:&lt;group&gt;</code> and CLI profiles{" "}
                <code className="font-mono">--org {org}</code>. Projects, members, tokens and global memories follow. Type the new
                slug to confirm.
              </>
            }
            confirmer="Change the slug"
            raison={{ label: "New slug", requise: true }}
            action={changeSlug}
          >
            Change the slug
          </GesteConfirme>
        </div>
      )}
    </Card>
  );
}
