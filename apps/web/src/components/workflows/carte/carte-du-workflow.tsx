// SPDX-License-Identifier: Apache-2.0
// lang: fr-ok-file alias:internal — former path of ../map/workflow-map, removed by S45-29
"use client";

import type { ComponentProps } from "react";
import { WorkflowMap } from "@/components/workflows/map/workflow-map";

type Props = ComponentProps<typeof WorkflowMap>;

export function CarteDuWorkflow({ acteurs, ...rest }: Omit<Props, "actors"> & { acteurs?: Props["actors"] }) {
  return <WorkflowMap {...rest} actors={acteurs} />;
}
