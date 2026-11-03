// SPDX-License-Identifier: Apache-2.0
"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { orgSection } from "@/components/shell/navigation";
import { PageHeader } from "@/components/page-header";

/**
 * The head of an organisation's page (S55-11): the icon and the hue of its entry in the sidebar, so
 * Agents, Skills or Spend are told apart at a glance the way a project's sections are (ADR 0120). A
 * page that is not in the sidebar gets a plain header.
 */
export function OrgPageHeader({
  title,
  eyebrow,
  description,
  actions,
}: {
  title: ReactNode;
  eyebrow?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  // Outside the router (a page rendered alone, in a test) there is no path: a plain header then.
  const section = orgSection(usePathname() ?? "");
  return (
    <PageHeader
      icon={section?.entry.icon}
      hue={section?.hue}
      eyebrow={eyebrow}
      title={title}
      description={description}
      actions={actions}
    />
  );
}
