// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { Eyebrow, Heading, TabList, tabClasses } from "@varga/design-system";

const TABS = [
  { suffix: "", label: "overview" },
  { suffix: "/board", label: "board" },
  { suffix: "/trains", label: "trains" },
  { suffix: "/findings", label: "findings" },
  { suffix: "/proposals", label: "proposals" },
  { suffix: "/memory", label: "memory" },
  { suffix: "/workflows", label: "workflows" },
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
  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <Eyebrow>project</Eyebrow>
        <Heading as="h1" size="xl">
          {slug}
        </Heading>
      </div>
      <TabList aria-label="project sections">
        {TABS.map((tab) => {
          const href = `/p/${slug}${tab.suffix}`;
          // Les intégrations (un sous-onglet par client), les propositions (une page par proposition)
          // et les workflows (une page par workflow) gardent leur onglet actif sous elles.
          const active = ["/integrations", "/proposals", "/workflows"].includes(tab.suffix)
            ? pathname.startsWith(href)
            : pathname === href;
          return (
            <Link key={tab.suffix} href={href} aria-current={active ? "page" : undefined} className={tabClasses(active)}>
              {tab.label}
            </Link>
          );
        })}
      </TabList>
      {children}
    </div>
  );
}
