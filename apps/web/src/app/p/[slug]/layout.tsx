"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { Eyebrow, Heading, TabList, tabClasses } from "@varga/design-system";

const TABS = [
  { suffix: "", label: "vue d'ensemble" },
  { suffix: "/board", label: "board" },
  { suffix: "/trains", label: "trains" },
  { suffix: "/findings", label: "findings" },
  { suffix: "/memory", label: "mémoire" },
  { suffix: "/workflow", label: "workflow" },
  { suffix: "/settings", label: "paramètres" },
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
        <Eyebrow>projet</Eyebrow>
        <Heading as="h1" size="xl">
          {slug}
        </Heading>
      </div>
      <TabList aria-label="sections du projet">
        {TABS.map((tab) => {
          const href = `/p/${slug}${tab.suffix}`;
          const active = pathname === href;
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
