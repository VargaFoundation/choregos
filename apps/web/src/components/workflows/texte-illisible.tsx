// SPDX-License-Identifier: Apache-2.0
import Link from "next/link";
import { usePathname } from "next/navigation";

/** Le YAML du brouillon ne se lit plus : la carte et la vue processus montrent la dernière version valide. */
export function TexteIllisible() {
  const pathname = usePathname();
  const yaml = pathname.replace(/\/(map)?$/, "") + "/yaml";
  return (
    <p className="rounded border border-warn px-3 py-2 text-sm text-warn" role="status" data-testid="texte-illisible">
      The draft&apos;s YAML does not read as a workflow: this shows the last valid version.{" "}
      <Link href={yaml} className="underline">
        fix it in the YAML tab
      </Link>
    </p>
  );
}
