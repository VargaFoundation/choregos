// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { TokensAndCostPanel } from "@/components/spend/tokens-panel";

/**
 * What a workflow spent (S64-07, ADR 0141 §1): the project's cost report narrowed to the work items
 * born in this workflow, by model, by stage, by agent and by run, in money or in tokens.
 */
export default function WorkflowSpendPage({ params }: { params: Promise<{ slug: string; name: string }> }) {
  const { slug, name: brut } = use(params);
  const name = decodeURIComponent(brut);
  return <TokensAndCostPanel project={slug} filter={{ workflow: name }} groups={["model", "stage", "agent", "run"]} />;
}
