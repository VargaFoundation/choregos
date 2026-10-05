// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { EditionCard } from "@/components/edition";
import { api } from "@/lib/api";

/**
 * L'édition qui tourne. En communautaire, la liste honnête de ce que l'édition entreprise ajoute :
 * du texte, pas de faux écran ni de bouton qui mènerait nulle part (ADR 0024, 0032).
 */
export default function EditionPage() {
  const edition = useQuery({ queryKey: ["edition"], queryFn: () => api.edition(), staleTime: Infinity, retry: false });
  return <EditionCard edition={edition.data} />;
}
