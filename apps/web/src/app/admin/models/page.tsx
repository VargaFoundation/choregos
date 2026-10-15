// SPDX-License-Identifier: Apache-2.0
"use client";

import { ModelCataloguePanel } from "@/components/model-catalogue";

/**
 * The organisation's model catalogue (S39-07, ADR 0060): which models its agents may use, for which
 * stage roles and usages, at which price, and which model backs each tier — whatever the gateway is.
 */
export default function ModelsPage() {
  return <ModelCataloguePanel />;
}
