// SPDX-License-Identifier: Apache-2.0

/**
 * Where a person reads one connector (owner review of 2026-10-28: "every source that uses a connector
 * says which, with a link"). An organisation connector is a card of Administration → Installed
 * connectors, marked and scrolled to by `?connector=`; a project connector is a card of the project's
 * Connections settings.
 */
export function orgConnectorHref(name: string): string {
  return `/admin/connectors?connector=${encodeURIComponent(name)}`;
}

export function projectConnectionsHref(slug: string): string {
  return `/p/${encodeURIComponent(slug)}/settings#connections`;
}
