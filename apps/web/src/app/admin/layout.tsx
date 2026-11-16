// SPDX-License-Identifier: Apache-2.0
"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Settings } from "lucide-react";
import { adminTrail, GROUP_HUES, useNavGroups, sectionOf } from "@/components/shell/navigation";
import { PageHeader } from "@/components/page-header";
import { BreadcrumbBase, Breadcrumbs, type BreadcrumbStep } from "@/components/ui";

const BASE: BreadcrumbStep[] = [{ href: "/admin", label: "Administration" }];

/**
 * The administration, in sub-pages: the core screens, then one page per section an extension declares
 * (ADR 0032) — the API only returns those one has the right to. No tab row (S30-03): the sidebar
 * lists the sections when one is inside the administration, the trail says which one.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { groups } = useNavGroups(pathname);
  const section = sectionOf(pathname, groups);
  const steps = adminTrail(pathname, section);
  return (
    <BreadcrumbBase.Provider value={BASE}>
      <div className="space-y-4">
        <Breadcrumbs steps={steps} />
        <PageHeader
          icon={section?.icon ?? Settings}
          hue={GROUP_HUES.configure}
          title={steps.length > 0 ? section!.label : "Administration"}
        />
        {children}
      </div>
    </BreadcrumbBase.Provider>
  );
}
