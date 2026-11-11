import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocsProvider, HelpEntry, LearnMore } from "@/components/docs-link";
import { PageHeader } from "@/components/page-header";
import { api } from "@/lib/api";
import { DEFAULT_DOCS_URL, DOC_SLUGS, DOCS_BY_ROUTE, docsBase, docsSlugFor, docsUrl } from "@/lib/docs";
import type { Edition } from "@/lib/types";

const pathname = vi.hoisted(() => ({ current: "/" as string | null }));
vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  usePathname: () => pathname.current,
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, edition: vi.fn() } };
});

const EDITION = { edition: "community", features: [], version: "0.25.3" } as unknown as Edition;

function wrap(children: ReactNode) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <DocsProvider>{children}</DocsProvider>
    </QueryClientProvider>
  );
}

describe("the documentation's pages and the screens that point to them (S62-11)", () => {
  it("every slug of the map is a page of the documentation", () => {
    const known = new Set<string>(DOC_SLUGS);
    for (const [route, slug] of DOCS_BY_ROUTE) expect(known.has(slug), `${route} → ${slug}`).toBe(true);
  });

  it("each key screen has its page, the most specific pattern winning", () => {
    const expected: Array<[string, string | null]> = [
      ["/", "use/projects-and-members"],
      ["/p/billing-api", "use/projects-and-members"],
      ["/p/billing-api/runs/r1", null],
      ["/p/billing-api/workflows/hotfix/yaml", "use/workflows"],
      ["/p/billing-api/board", "use/work-items"],
      ["/p/billing-api/items/42?tab=log", "use/work-items"],
      ["/inbox", "use/decisions"],
      ["/p/billing-api/inbox", "use/decisions"],
      ["/knowledge/runbooks", "use/knowledge"],
      ["/library/connectors", "use/connectors"],
      ["/agents/leas-claude", "build/agents"],
      ["/skills/library", "build/skills"],
      ["/library/plugins", "build/plugins"],
      ["/library/mcp-servers", "build/tools"],
      ["/integrations", "integrate/mcp"],
      ["/integrations/claude-code", "integrate/mcp"],
      ["/integrations/a2a", "integrate/a2a"],
      ["/p/billing-api/integrations/a2a", "integrate/a2a"],
      ["/integrations/cli", "cli/install"],
      ["/integrations/rest", "integrate/rest"],
      ["/p/billing-api/settings/people", "use/projects-and-members"],
      ["/p/billing-api/settings/security", "use/budgets-and-policies"],
      ["/costs", "use/budgets-and-policies"],
      ["/admin/identity", "host"],
      ["/account", null],
    ];
    for (const [route, slug] of expected) expect(docsSlugFor(route), route).toBe(slug);
    expect(docsSlugFor(null)).toBeNull();
  });

  it("the base is an http(s) address without its trailing slash, else the public site", () => {
    expect(docsBase("https://docs.intranet.example/choregos/")).toBe("https://docs.intranet.example/choregos");
    expect(docsBase("javascript:alert(1)")).toBe(DEFAULT_DOCS_URL);
    expect(docsBase(undefined)).toBe(DEFAULT_DOCS_URL);
    expect(docsUrl(null, "")).toBe("https://docs.varga.foundation/choregos/");
    expect(docsUrl("http://docs.local", "cli/install")).toBe("http://docs.local/cli/install");
  });
});

describe("Help and Learn more", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pathname.current = "/";
  });

  it("Help opens the documentation's home of this instance, in a new tab, reachable by keyboard", async () => {
    vi.mocked(api.edition).mockResolvedValue({ ...EDITION, docs_url: "https://docs.intranet.example/choregos" });
    render(wrap(<HelpEntry />));
    const help = screen.getByRole("link", { name: /^Help/ });
    expect(help).toHaveAttribute("href", "https://docs.varga.foundation/choregos/");
    await waitFor(() => expect(help).toHaveAttribute("href", "https://docs.intranet.example/choregos/"));
    expect(help).toHaveAttribute("target", "_blank");
    expect(help).toHaveAccessibleName("Help, documentation, opens in a new tab");
    expect(help.querySelector("svg")).not.toBeNull();
    help.focus();
    expect(help).toHaveFocus();
  });

  it("an instance that names no site keeps the public one", async () => {
    vi.mocked(api.edition).mockResolvedValue(EDITION);
    render(wrap(<HelpEntry />));
    await waitFor(() => expect(api.edition).toHaveBeenCalled());
    expect(screen.getByTestId("help-entry")).toHaveAttribute("href", "https://docs.varga.foundation/choregos/");
  });

  it("a page's header points to the screen's page; a screen the documentation does not cover has none", async () => {
    vi.mocked(api.edition).mockResolvedValue({ ...EDITION, docs_url: "https://docs.intranet.example/choregos" });
    pathname.current = "/p/billing-api/workflows/hotfix";
    const { unmount } = render(wrap(<PageHeader title="Hotfix" />));
    await waitFor(() =>
      expect(screen.getByTestId("learn-more")).toHaveAttribute("href", "https://docs.intranet.example/choregos/use/workflows"),
    );
    expect(screen.getByTestId("learn-more")).toHaveAccessibleName("Learn more, documentation, opens in a new tab");
    unmount();
    pathname.current = "/account";
    render(wrap(<PageHeader title="My account" />));
    expect(screen.queryByTestId("learn-more")).toBeNull();
  });

  it("a section's own header (h2) does not repeat it", () => {
    vi.mocked(api.edition).mockResolvedValue(EDITION);
    pathname.current = "/skills";
    render(wrap(<PageHeader title="A section" as="h2" />));
    expect(screen.queryByTestId("learn-more")).toBeNull();
    render(wrap(<LearnMore />));
    expect(screen.getByTestId("learn-more")).toHaveAttribute("href", "https://docs.varga.foundation/choregos/build/skills");
  });
});
