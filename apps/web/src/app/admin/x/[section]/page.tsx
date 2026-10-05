// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { use } from "react";
import { SectionRendue } from "@/components/admin/section";
import { Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/** Une section déclarée par un greffon, rendue par la console (ADR 0032). */
export default function SectionPage({ params }: { params: Promise<{ section: string }> }) {
  const { section: id } = use(params);
  const { org } = useSession();
  const sections = useQuery({ queryKey: ["admin-sections", org], queryFn: () => api.adminSections(org) });
  if (sections.error) return <ErrorNote>{String(sections.error)}</ErrorNote>;
  if (!sections.data) return <Empty>reading the section…</Empty>;
  const section = sections.data.find((candidate) => candidate.id === id);
  if (!section) return <Empty title="no such section">it is not declared here, or not for you.</Empty>;
  return <SectionRendue section={section} org={org} />;
}
