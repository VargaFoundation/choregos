// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { IntegrationsPage } from "@/components/integrations/page";

export default function Page({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return <IntegrationsPage client="claude-code" projet={slug} />;
}
