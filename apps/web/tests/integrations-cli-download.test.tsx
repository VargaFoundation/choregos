import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CliDownload } from "@/components/integrations/cli-download";
import { IntegrationsPanel } from "@/components/integrations/panel";
import { api, ApiError } from "@/lib/api";
import {
  type CliManifest,
  detectPlatform,
  firstCommands,
  formatSize,
  installCommand,
  recommendedFile,
  releaseUrl,
} from "@/lib/cli-download";
import type { Integrations } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, integrations: vi.fn(), cliManifest: vi.fn() } };
});

const SHA = (c: string) => c.repeat(64);
const MANIFEST: CliManifest = {
  version: "0.26.0",
  files: [
    { name: "choregos-linux-x86_64", os: "linux", arch: "x86_64", sha256: SHA("a"), size: 31_457_280 },
    { name: "choregos-linux-arm64", os: "linux", arch: "arm64", sha256: SHA("b"), size: 30_000_000 },
    { name: "choregos-macos-arm64", os: "macos", arch: "arm64", sha256: SHA("c"), size: 29_000_000 },
    { name: "choregos-macos-x86_64", os: "macos", arch: "x86_64", sha256: SHA("d"), size: 29_500_000 },
    { name: "choregos-windows-x86_64.exe", os: "windows", arch: "x86_64", sha256: SHA("e"), size: 32_000_000 },
  ],
};

const INFOS: Integrations = {
  mcp_url: "https://choregos.example.org/mcp",
  protocol_versions: ["2025-06-18"],
  oauth: { enabled: false },
  version: "0.26.0",
};

const WINDOWS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36";
const MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15";
const LINUX_ARM_UA = "Mozilla/5.0 (X11; Linux aarch64; rv:133.0) Gecko/20100101 Firefox/133.0";
const LINUX_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0";
const ANDROID_UA = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36";

function withUserAgent(userAgent: string, userAgentData?: unknown) {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(userAgent);
  Object.defineProperty(navigator, "userAgentData", { value: userAgentData, configurable: true });
}

function mount(node: React.ReactNode) {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

afterEach(() => {
  vi.restoreAllMocks();
  Object.defineProperty(navigator, "userAgentData", { value: undefined, configurable: true });
});

describe("the CLI the instance serves (S62-08), pure parts", () => {
  it("reads the system and processor the browser says", () => {
    expect(detectPlatform(WINDOWS_UA)).toEqual({ os: "windows", arch: "x86_64" });
    expect(detectPlatform(LINUX_UA)).toEqual({ os: "linux", arch: "x86_64" });
    expect(detectPlatform(LINUX_ARM_UA)).toEqual({ os: "linux", arch: "arm64" });
    // A Mac says "Intel" on Apple silicon too: unknown, unless the hints say.
    expect(detectPlatform(MAC_UA)).toEqual({ os: "macos", arch: null });
    expect(detectPlatform(MAC_UA, { platform: "macOS", architecture: "x86" })).toEqual({ os: "macos", arch: "x86_64" });
    expect(detectPlatform(WINDOWS_UA, { platform: "Windows", architecture: "arm" })).toEqual({ os: "windows", arch: "arm64" });
    expect(detectPlatform(ANDROID_UA)).toEqual({ os: null, arch: null });
    expect(detectPlatform("", { platform: "Linux" })).toEqual({ os: "linux", arch: null });
  });

  it("recommends the file of this machine, Apple silicon for an unknown Mac", () => {
    expect(recommendedFile(MANIFEST.files, { os: "linux", arch: "arm64" })?.name).toBe("choregos-linux-arm64");
    expect(recommendedFile(MANIFEST.files, { os: "macos", arch: null })?.name).toBe("choregos-macos-arm64");
    expect(recommendedFile(MANIFEST.files, { os: "linux", arch: null })?.name).toBe("choregos-linux-x86_64");
    // Windows on ARM runs the x86_64 build under emulation: the only Windows file.
    expect(recommendedFile(MANIFEST.files, { os: "windows", arch: "arm64" })?.name).toBe("choregos-windows-x86_64.exe");
    expect(recommendedFile(MANIFEST.files, { os: null, arch: null })).toBeNull();
  });

  it("writes the one-line install of each system, from this instance", () => {
    expect(installCommand("linux", "https://choregos.example.org/")).toBe(
      "curl -fsSL https://choregos.example.org/cli/install.sh | sh",
    );
    expect(installCommand("macos", "https://choregos.example.org")).toBe(
      "curl -fsSL https://choregos.example.org/cli/install.sh | sh",
    );
    expect(installCommand("windows", "https://choregos.example.org")).toBe(
      "irm https://choregos.example.org/cli/install.ps1 | iex",
    );
  });

  it("names the release, the sizes and the first commands", () => {
    expect(releaseUrl("0.26.0")).toBe("https://github.com/VargaFoundation/choregos/releases/tag/v0.26.0");
    expect(releaseUrl("v0.26.0")).toBe("https://github.com/VargaFoundation/choregos/releases/tag/v0.26.0");
    expect(formatSize(31_457_280)).toBe("30.0 MiB");
    expect(formatSize(2048)).toBe("2 KiB");
    expect(formatSize(12)).toBe("12 B");
    const posix = firstCommands("linux", "https://choregos.example.org", "acme");
    expect(posix).toContain("choregos login --api-url https://choregos.example.org --token <full-access token> --org acme");
    expect(posix).toContain("export CHOREGOS_API_URL=https://choregos.example.org CHOREGOS_TOKEN=");
    expect(firstCommands("windows", "https://choregos.example.org", "acme")).toContain(
      '$env:CHOREGOS_API_URL = "https://choregos.example.org"',
    );
  });
});

describe("the download block on Integrations › CLI", () => {
  it("offers the install command for this computer, every file with its checksum, then the first commands", async () => {
    withUserAgent(WINDOWS_UA);
    vi.mocked(api.cliManifest).mockResolvedValue(MANIFEST);
    mount(<CliDownload origin="https://choregos.example.org" version="0.26.0" org="acme" />);
    expect(await screen.findByTestId("cli-version")).toHaveTextContent("choregos 0.26.0");
    expect(screen.getByTestId("cli-install-command")).toHaveTextContent(
      "irm https://choregos.example.org/cli/install.ps1 | iex",
    );
    const table = screen.getByRole("table", { name: "CLI files" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(5);
    const link = within(table).getByRole("link", { name: "choregos-windows-x86_64.exe" });
    expect(link).toHaveAttribute("href", "/downloads/cli/choregos-windows-x86_64.exe");
    expect(link.closest("tr")).toHaveAttribute("data-recommended", "true");
    expect(within(table).getByText(SHA("c"))).toBeInTheDocument();
    expect(screen.getByTestId("cli-first-commands")).toHaveTextContent("choregos login --api-url https://choregos.example.org");
    expect(screen.getByText(/choregos self update/)).toBeInTheDocument();

    // Another system: its own command, and its own file first.
    fireEvent.change(screen.getByRole("combobox", { name: "System" }), { target: { value: "macos" } });
    expect(screen.getByTestId("cli-install-command")).toHaveTextContent(
      "curl -fsSL https://choregos.example.org/cli/install.sh | sh",
    );
    expect(within(table).getByRole("link", { name: "choregos-macos-arm64" }).closest("tr")).toHaveAttribute(
      "data-recommended",
      "true",
    );
  });

  it("refines a Mac's processor with the browser's high-entropy hints", async () => {
    withUserAgent(MAC_UA, {
      platform: "macOS",
      getHighEntropyValues: () => Promise.resolve({ architecture: "x86" }),
    });
    vi.mocked(api.cliManifest).mockResolvedValue(MANIFEST);
    mount(<CliDownload origin="https://choregos.example.org" version="0.26.0" org="acme" />);
    const link = await screen.findByRole("link", { name: "choregos-macos-x86_64" });
    await vi.waitFor(() => expect(link.closest("tr")).toHaveAttribute("data-recommended", "true"));
    expect(screen.getByTestId("cli-install-command")).toHaveTextContent("/cli/install.sh | sh");
  });

  it("says when this build bundles no CLI, and links the release of the version it runs", async () => {
    withUserAgent(LINUX_UA);
    vi.mocked(api.cliManifest).mockResolvedValue(null);
    mount(<CliDownload origin="https://choregos.example.org" version="0.26.0" org="acme" />);
    expect(await screen.findByTestId("cli-not-bundled")).toHaveTextContent("This build bundles no CLI");
    expect(screen.getByTestId("cli-release")).toHaveAttribute(
      "href",
      "https://github.com/VargaFoundation/choregos/releases/tag/v0.26.0",
    );
    expect(screen.queryByTestId("cli-install-command")).not.toBeInTheDocument();
  });

  it("without the instance's version, still points at the releases", async () => {
    vi.mocked(api.cliManifest).mockResolvedValue(null);
    mount(<CliDownload origin="https://choregos.example.org" org="acme" />);
    expect(await screen.findByTestId("cli-not-bundled")).toHaveTextContent("GitHub releases");
  });

  it("shows an unreadable manifest as an error", async () => {
    vi.mocked(api.cliManifest).mockRejectedValue(new ApiError(503, { detail: "the bundled manifest.json is invalid" }));
    mount(<CliDownload origin="https://choregos.example.org" version="0.26.0" org="acme" />);
    expect(await screen.findByText("the bundled manifest.json is invalid")).toBeInTheDocument();
  });

  it("is on the CLI tab of the Integrations page only, with the instance's origin", async () => {
    withUserAgent(LINUX_UA);
    vi.mocked(api.integrations).mockResolvedValue(INFOS);
    vi.mocked(api.cliManifest).mockResolvedValue(MANIFEST);
    mount(<IntegrationsPanel client="cli" base="/integrations" />);
    expect(await screen.findByTestId("cli-install-command")).toHaveTextContent(
      "curl -fsSL https://choregos.example.org/cli/install.sh | sh",
    );
  });

  it("is not on another client's tab", async () => {
    vi.mocked(api.integrations).mockResolvedValue(INFOS);
    vi.mocked(api.cliManifest).mockResolvedValue(MANIFEST);
    mount(<IntegrationsPanel client="cursor" base="/integrations" />);
    await screen.findByTestId("mcp-url");
    expect(screen.queryByText("Install the CLI")).not.toBeInTheDocument();
    expect(api.cliManifest).not.toHaveBeenCalled();
  });
});

describe("api.cliManifest reads the manifest at the root, not under /api/v1", () => {
  it("answers the manifest, null on a 404, and an error otherwise", async () => {
    const { api: real } = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(MANIFEST), { status: 200 }));
    await expect(real.cliManifest()).resolves.toEqual(MANIFEST);
    expect(fetchMock.mock.calls[0]![0]).toBe("/downloads/cli/manifest.json");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ code: "no_cli_bundled" }), { status: 404 }));
    await expect(real.cliManifest()).resolves.toBeNull();
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "unusable" }), { status: 503 }));
    await expect(real.cliManifest()).rejects.toThrow("unusable");
  });
});
