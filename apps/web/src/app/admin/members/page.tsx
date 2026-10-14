// SPDX-License-Identifier: Apache-2.0
"use client";

import { MembersTable } from "@/components/members-table";

/**
 * The organisation's members (S30-06): one row per person, their role in the organisation and
 * their projects. The last administrator cannot leave — the API refuses it (409), and the screen says so.
 */
export default function MembersPage() {
  return <MembersTable />;
}
