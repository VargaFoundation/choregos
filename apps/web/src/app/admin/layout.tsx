// SPDX-License-Identifier: Apache-2.0
"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useNavGroups, sectionOf } from "@/components/coquille/navigation";
import { BreadcrumbBase, Breadcrumbs, Heading, type BreadcrumbStep } from "@/components/ui";

const BASE: BreadcrumbStep[] = [{ href: "/admin", label: "Administration" }];

/**
 * The administration, in sub-pages: the core screens, then one page per section a plugin declares
 * (ADR 0032) — the API only returns those one has the right to. No tab row (S30-03): the sidebar
 * lists the sections when one is inside the administration, the trail says which one.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { groups } = useNavGroups(pathname);
  const section = sectionOf(pathname, groups);
  const steps = section && section.href !== "/admin" ? [{ label: section.label }] : [];
  return (
    <BreadcrumbBase.Provider value={BASE}>
      <div className="space-y-4">
        <Breadcrumbs steps={steps} />
        <Heading as="h1" size="xl">
          {steps.length > 0 ? section!.label : "Administration"}
        </Heading>
        {children}
      </div>
    </BreadcrumbBase.Provider>
  );
}
