// SPDX-License-Identifier: Apache-2.0
/**
 * The CLI the instance serves (S62-08): which file suits the browser's machine, the one-line install
 * for its system, and where the release is when this build bundles no CLI. Pure, so it is tested
 * without a browser; `components/integrations/cli-download.tsx` shows it.
 */

export type CliOs = "linux" | "macos" | "windows";
export type CliArch = "x86_64" | "arm64";

/** One binary of the manifest (the S62-07/08 shared contract). */
export type CliFile = { name: string; os: CliOs; arch: CliArch; sha256: string; size: number };
export type CliManifest = { version: string; files: CliFile[] };

/** The machine the browser runs on; `arch` is null when the browser does not say. */
export type BrowserPlatform = { os: CliOs | null; arch: CliArch | null };

export const CLI_RELEASES = "https://github.com/VargaFoundation/choregos/releases";
export const MANIFEST_PATH = "/downloads/cli/manifest.json";

export const OS_LABELS: Record<CliOs, string> = { linux: "Linux", macos: "macOS", windows: "Windows" };

/**
 * The machine, from what the browser says: `navigator.userAgentData` (platform, and the architecture
 * when the high-entropy hints answered) first, the user agent otherwise. A Mac's user agent says
 * "Intel" on Apple silicon too: its architecture is unknown unless the hints say it.
 */
export function detectPlatform(userAgent: string, hints: { platform?: string; architecture?: string } = {}): BrowserPlatform {
  const platform = (hints.platform ?? "").toLowerCase();
  const ua = userAgent.toLowerCase();
  const said = platform || ua;
  let os: CliOs | null = null;
  if (said.includes("mac") || said.includes("darwin")) os = "macos";
  else if (said.includes("win")) os = "windows";
  else if (said.includes("linux") && !said.includes("android")) os = "linux";

  let arch: CliArch | null = null;
  const architecture = (hints.architecture ?? "").toLowerCase();
  if (architecture === "arm") arch = "arm64";
  else if (architecture === "x86") arch = "x86_64";
  else if (/aarch64|arm64/.test(ua)) arch = "arm64";
  else if (os !== "macos" && /x86_64|x64|win64|wow64|amd64/.test(ua)) arch = "x86_64";
  return { os, arch };
}

/** The file to offer first: this OS and architecture; on a Mac whose processor is unknown, Apple silicon. */
export function recommendedFile(files: readonly CliFile[], platform: BrowserPlatform): CliFile | null {
  if (!platform.os) return null;
  const forOs = files.filter((file) => file.os === platform.os);
  const arch = platform.arch ?? (platform.os === "macos" ? "arm64" : "x86_64");
  return forOs.find((file) => file.arch === arch) ?? forOs[0] ?? null;
}

/** The one line that installs the CLI from this instance. */
export function installCommand(os: CliOs, origin: string): string {
  const base = origin.replace(/\/+$/, "");
  return os === "windows" ? `irm ${base}/cli/install.ps1 | iex` : `curl -fsSL ${base}/cli/install.sh | sh`;
}

/** The GitHub release of a version, where the same files are attached. */
export function releaseUrl(version: string): string {
  return `${CLI_RELEASES}/tag/v${version.replace(/^v/, "")}`;
}

/** A size as a person reads it. */
export function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KiB`;
  return `${bytes} B`;
}

/** The first commands once it is installed: the profile, then a check that it answers. */
export function firstCommands(os: CliOs, origin: string, org: string): string {
  const base = origin.replace(/\/+$/, "");
  const login = `choregos login --api-url ${base} --token <full-access token> --org ${org}`;
  if (os === "windows") {
    return [
      login,
      "choregos whoami",
      "",
      "# or, without a saved profile (a CI job, a container):",
      `$env:CHOREGOS_API_URL = "${base}"; $env:CHOREGOS_TOKEN = "<full-access token>"; $env:CHOREGOS_ORG = "${org}"`,
    ].join("\n");
  }
  return [
    login,
    "choregos whoami",
    "",
    "# or, without a saved profile (a CI job, a container):",
    `export CHOREGOS_API_URL=${base} CHOREGOS_TOKEN=<full-access token> CHOREGOS_ORG=${org}`,
  ].join("\n");
}
