// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { EditionCard } from "@/components/edition";
import { api } from "@/lib/api";

/**
 * The running edition. In the community edition, the honest list of what the enterprise edition adds:
 * text, no fake screen or button that would lead nowhere (ADR 0024, 0032).
 */
export default function EditionPage() {
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  return <EditionCard edition={edition.data} />;
}
