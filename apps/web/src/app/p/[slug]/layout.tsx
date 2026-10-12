// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Eyebrow, Heading, Onglets, tabClasses } from "@/components/ui";
import { api } from "@/lib/api";
import { useHasReleaseTrain } from "@/lib/release-train";

const TABS = [
  { suffix: "", label: "Overview" },
  { suffix: "/board", label: "Board" },
  { suffix: "/trains", label: "Trains" },
  { suffix: "/findings", label: "Findings" },
  { suffix: "/memory", label: "Memory" },
  { suffix: "/workflows", label: "Workflows" },
  { suffix: "/agents", label: "Agents" },
  { suffix: "/actions", label: "Actions" },
  { suffix: "/integrations", label: "Integrations" },
  { suffix: "/settings", label: "Settings" },
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
  const trains = useHasReleaseTrain(slug);
  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <Eyebrow>Project · {slug}</Eyebrow>
        <Heading as="h1" size="xl" data-donnee>
          {projet.data?.name ?? slug}
        </Heading>
      </div>
      <Onglets aria-label="Project sections">
        {TABS.filter((tab) => tab.suffix !== "/trains" || trains).map((tab) => {
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
