// SPDX-License-Identifier: Apache-2.0
/**
 * The icon of a connector: its brand when the product has a mark in simple-icons (CC0-1.0, vendored in
 * `brand-icon-paths.ts`, see `scripts/vendor-brand-icons.mjs`), else a lucide icon for what it does.
 *
 * Brand marks are drawn in `currentColor`, so they follow the theme: the console stays calm (ADR 0043)
 * and one variant serves both themes. The marks are trademarks of their owners and only name the product
 * (simple-icons DISCLAIMER). Slack and every Microsoft product are absent from simple-icons, as are
 * OpenAI and LiteLLM: they take a capability icon.
 *
 * Used by the project settings page, and meant for the catalogue and `/admin/connectors`.
 */
import {
  Bell,
  ChartLine,
  Brain,
  Cloud,
  Container,
  DoorClosedLocked,
  FlaskConical,
  GitBranch,
  IdCard,
  Laptop,
  Library,
  ListTodo,
  Search,
  MessageSquare,
  Plug,
  Rocket,
  Sparkles,
  SquareKanban,
  Truck,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { BRAND_PATHS, type Brand } from "@/components/brand-icon-paths";

export type ConnectorIconChoice =
  | { kind: "brand"; brand: Brand }
  | { kind: "lucide"; icon: LucideIcon; demo?: boolean };

/** Brand per `type`, whatever the kind (a type name is the product's). */
const BRAND_OF_TYPE: Record<string, Brand> = {
  "github-issues": "github",
  github: "github",
  "gitlab-issues": "gitlab",
  jira: "jira",
  confluence: "confluence",
  git: "git",
  argocd: "argo",
  k8s_job: "kubernetes",
  agent_sandbox: "kubernetes",
  local_docker: "docker",
  pfsense: "pfsense",
  cilium: "cilium",
  nginx: "nginx",
};

/** `tekton` is a brand as a CI and as a runtime. */
const BRAND_OF_KIND_AND_TYPE: Record<string, Brand> = { "ci/tekton": "tekton", "runtime/tekton": "tekton" };

const LUCIDE_OF_TYPE: Record<string, LucideIcon> = {
  internal: SquareKanban,
  aca: Cloud,
  slack: MessageSquare,
  sharepoint: Library,
  entra: IdCard,
};

/** What a kind does, for a type with no mark of its own. */
const LUCIDE_OF_KIND: Record<string, LucideIcon> = {
  tracker: ListTodo,
  scm: GitBranch,
  ci: Workflow,
  cd: Rocket,
  runtime: Container,
  gateway: Sparkles,
  memory: Brain,
  notify: Bell,
  documents: Library,
  identity: IdCard,
  mdm: Laptop,
  shipping: Truck,
  access_control: DoorClosedLocked,
  marketdata: ChartLine,
  web_search: Search,
};

/** A pure choice, so a test can pin every `(kind, type)` of the registry to an icon. */
export function connectorIconOf(kind: string, type: string): ConnectorIconChoice {
  if (type === "demo" || type === "fake") return { kind: "lucide", icon: FlaskConical, demo: true };
  if (kind === "mcp") return { kind: "brand", brand: "modelcontextprotocol" };
  const brand = BRAND_OF_KIND_AND_TYPE[`${kind}/${type}`] ?? BRAND_OF_TYPE[type];
  if (brand) return { kind: "brand", brand };
  return { kind: "lucide", icon: LUCIDE_OF_TYPE[type] ?? LUCIDE_OF_KIND[kind] ?? Plug };
}

export function ConnectorIcon({
  kind,
  type,
  className,
  size = 20,
}: {
  kind: string;
  type: string;
  className?: string;
  size?: number;
}) {
  const choice = connectorIconOf(kind, type);
  if (choice.kind === "lucide") {
    const Icon = choice.icon;
    return <Icon aria-hidden size={size} className={className} data-icon={`lucide:${kind}/${type}`} />;
  }
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="currentColor"
      className={className}
      data-icon={`brand:${choice.brand}`}
    >
      <path d={BRAND_PATHS[choice.brand]} />
    </svg>
  );
}
