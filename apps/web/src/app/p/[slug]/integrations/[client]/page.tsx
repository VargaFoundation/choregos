// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { IntegrationsPage } from "@/components/integrations/page";

export default function Page({ params }: { params: Promise<{ slug: string; client: string }> }) {
  const { slug, client } = use(params);
  return <IntegrationsPage client={client} projet={slug} />;
}
