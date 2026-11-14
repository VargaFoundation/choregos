// SPDX-License-Identifier: Apache-2.0
import type { LinkedAccount } from "@/lib/types";

export type ForgeProvider = LinkedAccount["provider"];

export const PROVIDER_LABELS: Record<ForgeProvider, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  forgejo: "Forgejo",
};

/** The instance a provider means when none is named; Forgejo is self-hosted only. */
export const DEFAULT_INSTANCES: Partial<Record<ForgeProvider, string>> = {
  github: "https://github.com",
  gitlab: "https://gitlab.com",
};

/** "GitHub", or "GitLab · gitlab.example" for an instance other than the provider's own. */
export function instanceLabel(provider: ForgeProvider, baseUrl: string): string {
  const name = PROVIDER_LABELS[provider] ?? provider;
  if (DEFAULT_INSTANCES[provider] === baseUrl) return name;
  return `${name} · ${baseUrl.replace(/^https?:\/\//, "")}`;
}

/** What the callback's `?link_error=` means, in words (ADR 0161). */
export const LINK_ERRORS: Record<string, string> = {
  handshake: "The link expired or was started in another session: start again.",
  denied: "The forge did not authorise the link.",
  taken: "That account is already linked to another member of the organisation.",
  forge_refused: "The forge refused the sign-in: an administrator checks the application's secret and callback URL.",
  forge_unreachable: "The forge could not be reached: an administrator checks the egress allowlist.",
  instance_gone: "That forge is no longer configured in the organisation.",
  instance_unusable: "That forge's configuration cannot be used: ask an administrator.",
};
