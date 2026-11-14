// SPDX-License-Identifier: Apache-2.0
/**
 * The publishing identity (ADR 0156): who publishes Choregos, where its source, releases,
 * documentation and plugins live. `publishing.json` beside this module is the copy of
 * `packages/core/src/choregos_core/publishing.json` that `tools/publishing_identity.py` keeps
 * identical; the console reads it here and never spells a value out elsewhere.
 */
import identity from "./publishing.json";

/** The core's repository: its releases, its issues, its files. */
export const SOURCE_REPOSITORY: string = identity.source_repository;
/** The release pages, where the CLI binaries are attached. */
export const RELEASES = `${SOURCE_REPOSITORY}/releases`;
/** The public user documentation, without a trailing slash. */
export const DOCUMENTATION: string = identity.documentation;
/** The repository the platform's own plugins are built and signed in, and their signed index. */
export const PLUGIN_REPOSITORY: string = identity.plugin_repository;
export const PLUGIN_INDEX: string = identity.plugin_index;

/** A file of the source repository's main branch, as GitHub shows it. */
export function sourceFile(path: string): string {
  return `${SOURCE_REPOSITORY}/blob/main/${path.replace(/^\/+/, "")}`;
}
