import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OrganisationTheme, ServedThemeProvider, THEME_STYLE_ID, useServedTheme } from "@/components/served-theme";
import { DEFAULT_SERVED_THEME } from "@/lib/served-theme";
import { SAMPLE_THEME } from "@/mocks/theme-sample";

vi.mock("@/lib/session", () => ({ useSession: () => ({ org: "acme" }) }));

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
});
