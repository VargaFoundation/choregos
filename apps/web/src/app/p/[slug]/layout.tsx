"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { cn } from "@/lib/cn";

const TABS = [
  { suffix: "", label: "Vue d'ensemble" },
  { suffix: "/board", label: "Board" },
  { suffix: "/trains", label: "Trains" },
  { suffix: "/findings", label: "Findings" },
  { suffix: "/memory", label: "Mémoire" },
  { suffix: "/workflow", label: "Workflow" },
  { suffix: "/settings", label: "Paramètres" },
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
    <div className="space-y-4">
      <nav className="flex flex-wrap gap-1 border-b border-line pb-2 text-sm">
        {TABS.map((tab) => {
          const href = `/p/${slug}${tab.suffix}`;
          const active = pathname === href;
          return (
            <Link
              key={tab.suffix}
              href={href}
              className={cn(
                "rounded px-3 py-1.5 no-underline",
                active ? "bg-surface font-medium text-ink" : "text-ink-muted hover:bg-surface",
              )}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>
      {children}
    </div>
  );
}
