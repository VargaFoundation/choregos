// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { Avatar } from "@/components/accounts/avatar";
import { Button, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";

/** What the API accepts (ADR 0162): the console refuses anything else before sending it. */
export const AVATAR_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const AVATAR_MAX_BYTES = 2 * 1024 * 1024;

/** Why a file is refused before it is sent, or `null` when it may go. */
export function avatarRefusal(file: Pick<File, "type" | "size">): string | null {
  if (!(AVATAR_TYPES as readonly string[]).includes(file.type)) return "A photo is a PNG, JPEG or WebP image.";
  if (file.size > AVATAR_MAX_BYTES) return "A photo is at most 2 MiB.";
  return null;
}

/**
 * My photo (S66-42, ADR 0162): the person uploads it here, the API re-encodes it and serves it on this origin;
 * without one the console shows their initials. It is never taken from the identity provider.
 */
export function AvatarEditor({ name, url }: { name: string; url: string | null | undefined }) {
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(gesture: () => Promise<unknown>) {
    setError(null);
    setBusy(true);
    try {
      await gesture();
      await client.invalidateQueries({ queryKey: ["me"] });
      await client.invalidateQueries({ queryKey: ["member-profile"] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Refused");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  function chosen(file: File | undefined) {
    if (!file) return;
    const refusal = avatarRefusal(file);
    if (refusal) {
      setError(refusal);
      if (input.current) input.current.value = "";
      return;
    }
    void act(() => api.putMyAvatar(file));
  }

  return (
    <div className="flex items-center gap-4">
      <Avatar name={name} url={url} size="lg" />
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={inputId} className="sr-only">
            Choose a photo
          </label>
          <input
            ref={input}
            id={inputId}
            type="file"
            accept={AVATAR_TYPES.join(",")}
            className="sr-only"
            data-testid="avatar-input"
            onChange={(event) => chosen(event.target.files?.[0])}
          />
          <Button size="sm" disabled={busy} onClick={() => input.current?.click()}>
            {url ? "Change photo" : "Upload a photo"}
          </Button>
          {url && (
            <Button size="sm" disabled={busy} onClick={() => void act(() => api.deleteMyAvatar())}>
              Remove photo
            </Button>
          )}
        </div>
        <p className="text-xs text-ink-muted">PNG, JPEG or WebP, at most 2 MiB. Members of your organisations see it.</p>
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </div>
  );
}
