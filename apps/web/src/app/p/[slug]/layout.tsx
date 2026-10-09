// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Eyebrow, Heading, tabClasses } from "@varga/design-system";
import { Onglets } from "@/components/ui";
import { api } from "@/lib/api";

const TABS = [
  { suffix: "", label: "overview" },
  { suffix: "/board", label: "board" },
  { suffix: "/trains", label: "trains" },
  { suffix: "/findings", label: "findings" },
  { suffix: "/memory", label: "memory" },
  { suffix: "/workflows", label: "workflows" },
  { suffix: "/agents", label: "agents" },
  { suffix: "/actions", label: "actions" },
  { suffix: "/integrations", label: "integrations" },
  { suffix: "/settings", label: "settings" },
];

export default function ProjectLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const pathname = usePathname();
  // Le titre est le NOM du projet (« Billing API »), comme sur sa carte dans la liste ; le slug
  // reste lisible au-dessus, c'est celui des URL et de la CLI (S23-04). Même clé que la vue
  // d'ensemble : une seule lecture.
  const projet = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <Eyebrow>project · {slug}</Eyebrow>
        <Heading as="h1" size="xl">
          {projet.data?.name ?? slug}
        </Heading>
      </div>
      <Onglets aria-label="project sections">
        {TABS.map((tab) => {
          const href = `/p/${slug}${tab.suffix}`;
          // Les intégrations (un sous-onglet par client), les actions (une page par action) et les
          // workflows (une page par workflow) gardent leur onglet actif sous elles ; un ticket et ses
          // runs sont ceux du board (S23-12 : leurs pages n'avaient aucun onglet actif).
          const active = ["/integrations", "/workflows", "/actions"].includes(tab.suffix)
            ? pathname.startsWith(href)
            : tab.suffix === "/board"
              ? pathname === href || pathname.startsWith(`/p/${slug}/items/`) || pathname.startsWith(`/p/${slug}/runs/`)
              : pathname === href;
          return (
            <Link
              key={tab.suffix}
              href={href}
              aria-current={active ? "page" : undefined}
              className={tabClasses(active)}
            >
              {tab.label}
            </Link>
          );
        })}
      </Onglets>
      {children}
    </div>
  );
}
