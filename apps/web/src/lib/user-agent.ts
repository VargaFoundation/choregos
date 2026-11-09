// SPDX-License-Identifier: Apache-2.0
/**
 * A `User-Agent` in a person's words: "Chrome on Windows", on a computer, a phone or a tablet. Enough
 * to recognise one's own browser in a list of sessions; not a fingerprint, and not a promise: the
 * header is what the browser says of itself.
 */
export type Device = "computer" | "phone" | "tablet";

export interface BrowserDescription {
  browser: string;
  os: string;
  device: Device;
}

const BROWSERS: Array<[RegExp, string]> = [
  [/\bEdg(?:e|A|iOS)?\//, "Edge"],
  [/\b(?:OPR|Opera)\//, "Opera"],
  [/\bSamsungBrowser\//, "Samsung Internet"],
  [/\b(?:Firefox|FxiOS)\//, "Firefox"],
  [/\b(?:Chrome|CriOS|Chromium)\//, "Chrome"],
  [/\bVersion\/[\d.]+.*\bSafari\//, "Safari"],
  [/\bcurl\//, "curl"],
];

const SYSTEMS: Array<[RegExp, string]> = [
  [/\b(?:iPhone|iPad|iPod)\b|\biOS\b/, "iOS"],
  [/\bAndroid\b/, "Android"],
  [/\bCrOS\b/, "ChromeOS"],
  [/\bWindows\b/, "Windows"],
  [/\bMac OS X\b|\bMacintosh\b/, "macOS"],
  [/\bLinux\b/, "Linux"],
];

export function describeBrowser(userAgent: string | null | undefined): BrowserDescription {
  const ua = userAgent ?? "";
  const browser = BROWSERS.find(([pattern]) => pattern.test(ua))?.[1] ?? "Unknown browser";
  const os = SYSTEMS.find(([pattern]) => pattern.test(ua))?.[1] ?? "an unknown system";
  const device: Device = /\biPad\b|\bTablet\b|Android(?!.*\bMobile\b)/.test(ua)
    ? "tablet"
    : /\b(?:iPhone|iPod|Mobile)\b/.test(ua)
      ? "phone"
      : "computer";
  return { browser, os, device };
}

/** "Chrome on Windows". */
export function browserLabel(userAgent: string | null | undefined): string {
  const { browser, os } = describeBrowser(userAgent);
  return `${browser} on ${os}`;
}
