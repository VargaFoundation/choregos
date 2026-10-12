// SPDX-License-Identifier: Apache-2.0
"use client";

import { MembersTable } from "@/components/members-table";

/**
 * Les membres de l'organisation (S30-06) : une ligne par personne, son rôle dans l'organisation et
 * ses projets. Le dernier administrateur ne se retire pas — l'API le refuse (409), et l'écran le dit.
 */
export default function MembersPage() {
  return <MembersTable />;
}
