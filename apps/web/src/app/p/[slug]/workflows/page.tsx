// SPDX-License-Identifier: Apache-2.0
"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { use } from "react";
import { Badge, buttonClasses } from "@varga/design-system";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { WorkflowRouting } from "@/lib/types";

/** Les conditions d'une règle de routage, en clair : « étiqueté incident », « type Bug ». */
function condition(rule: WorkflowRouting["rules"][number]): string {
  const parts: string[] = [];
  if (rule.when.labels_any?.length)
    parts.push(`labelled ${rule.when.labels_any.join(" or ")}`);
  if (rule.when.labels_all?.length)
    parts.push(`labelled ${rule.when.labels_all.join(" and ")}`);
  if (rule.when.item_type) parts.push(`of type ${rule.when.item_type}`);
  return parts.join(", ") || "always";
}

/**
 * Les workflows du projet (ADR 0031) : un projet en porte autant qu'il a de processus — l'arrivée et
 * le départ d'un projet RH, la livraison et le correctif d'une équipe produit. Chaque carte dit sa
 * version active, si c'est le défaut, ce qui y est routé et combien de tickets y sont épinglés.
 */
export default function WorkflowsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const workflows = useQuery({
    queryKey: ["workflows", slug],
    queryFn: () => api.workflows(slug),
  });
  const routing = useQuery({
    queryKey: ["workflow-routing", slug],
    queryFn: () => api.workflowRouting(slug),
  });

  if (workflows.error) return <ErrorNote>{String(workflows.error)}</ErrorNote>;
  const rules = routing.data?.rules ?? [];
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <p className="max-w-2xl text-sm text-ink-muted">
          each workflow is a process of its own; a request is born in one — the
          one it names, else the first routing rule it matches, else the default
          — and stays pinned to that version until it ends.
        </p>
        <Link
          href={`/p/${slug}/workflows/new`}
          className={buttonClasses("secondary", "sm")}
        >
          new workflow
        </Link>
      </div>

      {workflows.data?.length === 0 && (
        <Empty title="no workflow">publish one from a template.</Empty>
      )}
      <ul className="grid gap-4 md:grid-cols-2" aria-label="workflows">
        {workflows.data?.map((workflow) => {
          const routed = rules.filter(
            (rule) => rule.workflow === workflow.name,
          );
          return (
            <li key={workflow.name}>
              <Card
                title={
                  <Link
                    href={`/p/${slug}/workflows/${encodeURIComponent(workflow.name)}`}
                    className="hover:underline"
                  >
                    {workflow.name}
                  </Link>
                }
                action={
                  workflow.is_default ? (
                    <Badge tone="accent">default</Badge>
                  ) : undefined
                }
              >
                <dl
                  className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm"
                  data-testid={`workflow-card-${workflow.name}`}
                >
                  <dt className="text-ink-muted">active version</dt>
                  <dd>v{workflow.version}</dd>
                  <dt className="text-ink-muted">open items</dt>
                  <dd>{workflow.open_items}</dd>
                  <dt className="text-ink-muted">routed here</dt>
                  <dd>
                    {routed.length > 0
                      ? routed.map(condition).join("; ")
                      : workflow.is_default
                        ? "whatever no rule claims"
                        : "only when a request names it"}
                  </dd>
                  {workflow.created_by && (
                    <>
                      <dt className="text-ink-muted">published</dt>
                      <dd>
                        {workflow.created_by}
                        {workflow.updated_at
                          ? `, ${shortDate(workflow.updated_at)}`
                          : ""}
                      </dd>
                    </>
                  )}
                </dl>
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
