// SPDX-License-Identifier: Apache-2.0
// A skill file is UTF-8 text, except an inert binary (a font, an image), which is always base64.
// The extension alone decides, as on the server (ADR 0050).
const BINARY_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "ico", "ttf", "otf", "woff", "woff2"];

export function isBinarySkillFile(path: string): boolean {
  const dot = path.lastIndexOf(".");
  return dot >= 0 && BINARY_EXTENSIONS.includes(path.slice(dot + 1).toLowerCase());
}

/** What the console shows in place of the content of a file: its decoded size, never base64. */
export function skillFileLabel(path: string, value: string | undefined): string | null {
  if (!isBinarySkillFile(path)) return null;
  const compact = (value ?? "").trim();
  const padding = compact.endsWith("==") ? 2 : compact.endsWith("=") ? 1 : 0;
  return `binary, ${Math.max(0, Math.floor((compact.length * 3) / 4) - padding)} bytes`;
}
