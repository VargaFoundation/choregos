// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { IntegrationsPage } from "@/components/integrations/page";

export default function Page({ params }: { params: Promise<{ client: string }> }) {
  const { client } = use(params);
  return <IntegrationsPage client={client} />;
}
