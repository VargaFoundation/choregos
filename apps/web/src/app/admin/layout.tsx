// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Heading, tabClasses } from "@varga/design-system";
import { Onglets } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

const ONGLETS = [
  { href: "/admin", label: "overview" },
  { href: "/admin/members", label: "members" },
  { href: "/admin/connectors", label: "connectors" },
  { href: "/admin/audit", label: "audit" },
  { href: "/admin/platform", label: "platform" },
  { href: "/admin/edition", label: "edition" },
];

/**
 * L'administration, en sous-pages : les écrans du cœur, puis une page par section qu'un greffon
 * déclare (ADR 0032) — l'API ne rend que celles dont on a le droit.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { org } = useSession();
  const sections = useQuery({ queryKey: ["admin-sections", org], queryFn: () => api.adminSections(org), retry: false });
  const onglets = [
    ...ONGLETS,
    ...(sections.data ?? []).map((section) => ({ href: `/admin/x/${section.id}`, label: section.title })),
  ];
  return (
    <div className="space-y-4">
      <Heading as="h1" size="xl">
        administration
      </Heading>
      <Onglets aria-label="administration sections">
        {onglets.map((onglet) => {
          const actif = pathname === onglet.href;
          return (
            <Link key={onglet.href} href={onglet.href} aria-current={actif ? "page" : undefined} className={tabClasses(actif)}>
              {onglet.label}
            </Link>
          );
        })}
      </Onglets>
      {children}
    </div>
  );
}
