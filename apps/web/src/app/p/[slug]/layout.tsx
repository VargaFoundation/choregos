// SPDX-License-Identifier: Apache-2.0
"use client";

import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { projectTrail } from "@/components/coquille/navigation";
import { BreadcrumbBase, Breadcrumbs, Heading, type BreadcrumbStep } from "@/components/ui";
import { api } from "@/lib/api";

export default function ProjectLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const pathname = usePathname();
  // The title is the project's NAME ("Billing API"), as on its card in the list (S23-04). Same key
  // as the overview and the sidebar: a single read.
  const projet = useQuery({ queryKey: ["project", slug], queryFn: () => api.project(slug) });
  const nom = projet.data?.name ?? slug;
  // No tab row any more (S30-03): the sidebar lists the project's sections, the trail says where one is.
  const base: BreadcrumbStep[] = [
    { href: "/", label: "Projects" },
    { href: `/p/${slug}`, label: <span data-donnee>{nom}</span> },
  ];
  const trail = projectTrail(slug, pathname);
  return (
    <BreadcrumbBase.Provider value={base}>
      <div className="space-y-6">
        {trail && <Breadcrumbs steps={trail} />}
        <Heading as="h1" size="xl" data-donnee>
          {nom}
        </Heading>
        {children}
      </div>
    </BreadcrumbBase.Provider>
  );
}
