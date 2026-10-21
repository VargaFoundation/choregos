import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OrganisationTheme, ServedThemeProvider, THEME_STYLE_ID, useServedTheme } from "@/components/served-theme";
import { DEFAULT_SERVED_THEME } from "@/lib/served-theme";
import { SAMPLE_THEME } from "@/mocks/theme-sample";

let session: { org: string; orgs: { slug: string }[] } = { org: "acme", orgs: [{ slug: "acme" }] };
vi.mock("@/lib/session", () => ({ useSession: () => session }));

function Name() {
  return <span data-testid="name">{useServedTheme().product_name}</span>;
}

function page() {
  const style = document.createElement("style");
  style.id = THEME_STYLE_ID;
  document.head.append(style);
  render(
    <ServedThemeProvider initial={DEFAULT_SERVED_THEME}>
      <OrganisationTheme />
      <Name />
    </ServedThemeProvider>,
  );
  return style;
}

afterEach(() => {
  document.getElementById(THEME_STYLE_ID)?.remove();
  delete document.documentElement.dataset.defaultTheme;
  document.head.querySelectorAll('link[rel="icon"]').forEach((link) => link.remove());
  window.localStorage.clear();
  session = { org: "acme", orgs: [{ slug: "acme" }] };
  vi.unstubAllGlobals();
});

describe("an organisation's own theme replaces the instance's after hydration (S39-03b)", () => {
  it("asks for ?org=<slug> and swaps the style, the name and the default mode", async () => {
    const fetcher = vi.fn(async (_url: string) => new Response(JSON.stringify(SAMPLE_THEME), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const style = page();
    await waitFor(() => expect(screen.getByTestId("name").textContent).toBe("Sample"));
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/theme?org=acme");
    expect(style.textContent).toContain("--choregos-accent:#0b6e4f");
    expect(document.documentElement.dataset.defaultTheme).toBe("light");
  });

  it("keeps the instance theme when the request fails or the answer is not a theme", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 500 })));
    const style = page();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByTestId("name").textContent).toBe("Choregos");
    expect(style.textContent).toBe("");
  });

  it("swaps the favicon link of the page", async () => {
    const link = document.createElement("link");
    link.rel = "icon";
    link.href = "/api/v1/instance.svg";
    document.head.append(link);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SAMPLE_THEME), { status: 200 })));
    page();
    await waitFor(() => expect(link.getAttribute("href")).toBe(SAMPLE_THEME.favicon));
    expect(document.head.querySelectorAll('link[rel="icon"]')).toHaveLength(1);
  });

  it("creates the favicon link when the page had none", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SAMPLE_THEME), { status: 200 })));
    page();
    await waitFor(() => expect(document.head.querySelector('link[rel="icon"]')?.getAttribute("href")).toBe(SAMPLE_THEME.favicon));
  });

  it("drops the favicon link when the organisation theme has no favicon", async () => {
    const link = document.createElement("link");
    link.rel = "icon";
    link.href = "/api/v1/instance.svg";
    document.head.append(link);
    const sans = { ...SAMPLE_THEME, favicon: undefined };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(sans), { status: 200 })));
    page();
    await waitFor(() => expect(document.head.querySelector('link[rel="icon"]')).toBeNull());
  });

  it("before the session knows the organisations, asks for the remembered one (the sign-in page)", async () => {
    session = { org: "default", orgs: [] };
    window.localStorage.setItem("choregos.org", "acme-labs");
    const fetcher = vi.fn(async (_url: string) => new Response(JSON.stringify(SAMPLE_THEME), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    page();
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/theme?org=acme-labs");
  });

  it("asks for nothing when no organisation is known or remembered, or the remembered value is not a slug", async () => {
    session = { org: "default", orgs: [] };
    const fetcher = vi.fn(async () => new Response(JSON.stringify(SAMPLE_THEME), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    window.localStorage.setItem("choregos.org", "../../etc?x=1");
    page();
    await new Promise((r) => setTimeout(r, 20));
    expect(fetcher).not.toHaveBeenCalled();
  });
});
