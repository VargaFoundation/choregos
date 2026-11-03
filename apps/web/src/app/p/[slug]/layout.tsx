// SPDX-License-Identifier: Apache-2.0
"use client";

import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { projectSection, projectTrail } from "@/components/coquille/navigation";
import { PageHeader } from "@/components/page-header";
import {
  BreadcrumbBase,
  Breadcrumbs,
  type BreadcrumbStep,
} from "@/components/ui";
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
  const projet = useQuery({
    queryKey: ["project", slug],
    queryFn: () => api.project(slug),
  });
  const nom = projet.data?.name ?? slug;
  // No tab row any more (S30-03): the sidebar lists the project's sections, the trail says where one is.
  const base: BreadcrumbStep[] = [
    { href: "/", label: "Projects" },
    { href: `/p/${slug}`, label: <span data-donnee>{nom}</span> },
  ];
  const trail = projectTrail(slug, pathname);
  // The header names the section one is in, in its family's colour, under the project's name (S55-02):
  // "FON · Board", not "FON" over every page. The overview is the project itself.
  const section = projectSection(slug, pathname);
  const overview =
    !section || section.entry.href === `/p/${encodeURIComponent(slug)}`;
  return (
    <BreadcrumbBase.Provider value={base}>
      <div className="space-y-6">
        {trail && <Breadcrumbs steps={trail} />}
        {/* A ticket and a run write their own trail and title (their key, their name): no section header
            above them (S55-05). */}
        {trail && (
          <PageHeader
            icon={section?.entry.icon}
            hue={section?.hue}
            eyebrow={overview ? undefined : <span data-donnee>{nom}</span>}
            title={
              overview ? <span data-donnee>{nom}</span> : section.entry.label
            }
          />
        )}
        {children}
      </div>
    </BreadcrumbBase.Provider>
  );
}
