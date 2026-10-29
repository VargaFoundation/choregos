// SPDX-License-Identifier: Apache-2.0
"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { use, useEffect } from "react";
import { SectionRendue } from "@/components/admin/section";
import { isIdentitySection } from "@/components/coquille/navigation";
import { Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/** Une section déclarée par un greffon, rendue par la console (ADR 0032). */
export default function SectionPage({ params }: { params: Promise<{ section: string }> }) {
  const { section: id } = use(params);
  const { org } = useSession();
  const sections = useQuery({ queryKey: ["admin-sections", org], queryFn: () => api.adminSections(org) });
  const router = useRouter();
  const section = sections.data?.find((candidate) => candidate.id === id);
  // An identity section lives on the Identity page (S44-04): an old link lands there.
  const folded = !!section && isIdentitySection(section);
  useEffect(() => {
    if (folded) router.replace(`/admin/identity#${id}`);
  }, [folded, id, router]);
  if (sections.error) return <ErrorNote>{String(sections.error)}</ErrorNote>;
  if (!sections.data) return <Empty>Reading the section…</Empty>;
  if (!section) return <Empty title="No such section">It is not declared here, or not for you.</Empty>;
  if (folded) return <Empty>Opening the Identity page…</Empty>;
  return <SectionRendue section={section} org={org} />;
}
