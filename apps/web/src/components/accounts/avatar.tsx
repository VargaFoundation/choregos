// SPDX-License-Identifier: Apache-2.0
"use client";

import { useState } from "react";
import { cn } from "@/lib/cn";

/** Up to two initials, from the name, else from the address. */
export function initials(name: string): string {
  const words = name.split(/[\s@._-]+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}

const SIZES = {
  sm: "size-7 text-[11px]",
  md: "size-12 text-base",
  lg: "size-20 text-2xl",
} as const;

/**
 * A person's photo (ADR 0162), or their initials without one. The photo is the one the person uploaded,
 * served by the API on this origin (`avatar_url`): the console loads no image from an identity provider or a
 * forge (`img-src 'self'`). A photo that fails to load falls back to the initials.
 */
export function Avatar({
  name,
  url,
  size = "md",
  className,
}: {
  /** The person's name, else their address: the initials and the image's alternative text. */
  name: string;
  url?: string | null;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const shown = url && failed !== url ? url : null;
  const frame = cn(
    "flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-line bg-surface-muted font-medium text-ink",
    SIZES[size],
    className,
  );
  if (shown) {
    return (
      <span className={frame}>
        {/* A served file, same-origin: `next/image` would add an optimiser in front of it. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={shown} alt={name} className="size-full object-cover" onError={() => setFailed(shown)} data-testid="avatar-photo" />
      </span>
    );
  }
  return (
    <span aria-hidden className={frame} data-testid="avatar-initials">
      {initials(name)}
    </span>
  );
}
