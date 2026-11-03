// SPDX-License-Identifier: Apache-2.0
/**
 * A template's YAML, renamed: `metadata.name` is the workflow's name in the project, and the API
 * refuses a YAML whose name is not the route's (ADR 0031). The rest of the text does not move.
 */
export function rename(yaml: string, name: string): string {
  return yaml.replace(/(metadata:\s*\{?\s*name:\s*)[^,\s}]+/, `$1${name}`);
}
