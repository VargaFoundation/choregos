// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote, Input } from "@/components/ui";
import { api } from "@/lib/api";

/**
 * A member's GitHub link (S47-15): the numeric user id the signed comments carry, and the login
 * shown next to it. The console never calls api.github.com: the hint only links to it.
 */
export function TrackerIdentities({ org, userId }: { org: string; userId: string }) {
  const client = useQueryClient();
  const key = ["tracker-identities", org, userId];
  const identities = useQuery({ queryKey: key, queryFn: () => api.trackerIdentities(org, userId) });
  const linked = identities.data?.find((i) => i.tracker === "github");
  const [draft, setDraft] = useState<{ login: string; id: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const login = draft?.login ?? linked?.login ?? "";
  const id = draft?.id ?? linked?.external_id ?? "";

  async function act(gesture: () => Promise<unknown>) {
    setError(null);
    try {
      await gesture();
      setDraft(null);
      await client.invalidateQueries({ queryKey: key });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    }
  }

  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">GitHub login</span>
          <Input value={login} onChange={(event) => setDraft({ login: event.target.value, id })} className="w-auto" />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-ink-muted">GitHub user id</span>
          <Input
            inputMode="numeric"
            value={id}
            onChange={(event) => setDraft({ login, id: event.target.value.replace(/\D/g, "") })}
            className="w-auto"
          />
        </label>
        <Button
          size="sm"
          tone="primary"
          disabled={!login.trim() || !id}
          onClick={() => void act(() => api.setTrackerIdentity(org, userId, "github", { external_id: id, login: login.trim() }))}
        >
          Save
        </Button>
        {linked && (
          <Button size="sm" onClick={() => void act(() => api.deleteTrackerIdentity(org, userId, "github"))}>
            Remove
          </Button>
        )}
      </div>
      <p className="text-xs text-ink-muted">
        The id is the number at{" "}
        <a className="underline" href="https://api.github.com/users/<login>" target="_blank" rel="noreferrer">
          https://api.github.com/users/&lt;login&gt;
        </a>
        . Only pause, resume and stop are taken from signed GitHub comments.
      </p>
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}
