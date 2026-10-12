// SPDX-License-Identifier: Apache-2.0
"use client";

import { use } from "react";
import { MembersTable } from "@/components/members-table";

/** A project's members (S30-06): managed by its project owner, with the roles of a project. */
export default function ProjectMembersPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = use(params);
  return <MembersTable project={slug} />;
}
