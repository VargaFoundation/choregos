// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { InstallationDetail } from "@/components/assistants/installation-detail";

/** One installation, for its owners (S63-18). */
export default function InstallationPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return <InstallationDetail slug={slug} />;
}
