// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { use, type ReactNode } from "react";
import { Badge, tabClasses } from "@varga/design-system";
import { Onglets } from "@/components/ui";
import { BarreDuBrouillon } from "@/components/workflows/barre-du-brouillon";
import { BrouillonProvider } from "@/components/workflows/brouillon";
import { useWorkflow } from "@/components/workflows/use-workflow";

const VUES = [
  { suffix: "", label: "process" },
  { suffix: "/map", label: "map" },
  { suffix: "/yaml", label: "YAML" },
  { suffix: "/history", label: "history" },
];

/** Un workflow du projet, par son nom : son processus, sa carte, son YAML, son historique. */
export default function WorkflowLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ slug: string; name: string }>;
}) {
  const { slug, name: brut } = use(params);
  const name = decodeURIComponent(brut);
  const pathname = usePathname();
  const { definition } = useWorkflow(slug, name);
  const base = `/p/${slug}/workflows/${encodeURIComponent(name)}`;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <Link
          href={`/p/${slug}/workflows`}
          className="text-sm text-ink-muted hover:underline"
        >
          workflows
        </Link>
        <span aria-hidden className="text-ink-muted">
          /
        </span>
        <h2 className="text-lg font-medium">{name}</h2>
        {definition.data && (
          <span className="text-sm text-ink-muted">
            v{definition.data.version}
          </span>
        )}
        {definition.data?.is_default && <Badge tone="accent">default</Badge>}
      </div>
      <Onglets aria-label={`views of ${name}`}>
        {VUES.map((vue) => {
          const href = `${base}${vue.suffix}`;
          const active = pathname === href;
          return (
            <Link
              key={vue.suffix}
              href={href}
              aria-current={active ? "page" : undefined}
              className={tabClasses(active)}
            >
              {vue.label}
            </Link>
          );
        })}
      </Onglets>
      {/* Le brouillon est commun à la carte et à la vue processus : un geste sur l'une se voit sur
          l'autre, et rien n'est publié avant « publish ». */}
      <BrouillonProvider slug={slug} name={name}>
        <BarreDuBrouillon />
        {children}
      </BrouillonProvider>
    </div>
  );
}
