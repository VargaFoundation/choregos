// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { use } from "react";
import { FlowScreen } from "@/components/workflows/flow/flow-screen";
import { api } from "@/lib/api";

/** A workflow's flow: its stations and the work items travelling through them, live (S67-29). */
export default function WorkflowFlowPage({ params }: { params: Promise<{ slug: string; name: string }> }) {
  const { slug, name: raw } = use(params);
  const name = decodeURIComponent(raw);
  const search = useSearchParams();
  const workflows = useQuery({ queryKey: ["workflows", slug], queryFn: () => api.workflows(slug) });
  const isDefault = workflows.data?.find((w) => w.name === name)?.is_default ?? false;
  return <FlowScreen slug={slug} workflow={name} isDefault={isDefault} wallRequested={search.get("wall") === "1"} />;
}
