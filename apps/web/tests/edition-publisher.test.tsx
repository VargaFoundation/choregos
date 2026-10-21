import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EditionBadge, EditionCard, EditionFooterLine, FooterText } from "@/components/edition";
import { ServedThemeProvider } from "@/components/served-theme";
import { DEFAULT_SERVED_THEME } from "@/lib/served-theme";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, edition: vi.fn() } };
});

const ENTERPRISE = {
  edition: "enterprise" as const,
  features: ["saml"],
  version: "0.17.16",
  publisher: "GraalSystems",
  edition_version: "0.2.0",
};
const COMMUNITY = { edition: "community" as const, features: [], version: "0.17.16", publisher: null, edition_version: null };

function renderFooter(productName?: string) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EditionFooterLine productName={productName} />
    </QueryClientProvider>,
  );
}

describe("the edition names its publisher", () => {
  beforeEach(() => vi.mocked(api.edition).mockReset());

  it("the admin card shows the edition version, the publisher and the core version", () => {
    render(<EditionCard edition={ENTERPRISE} />);
    expect(screen.getByText(/published by GraalSystems/)).toBeInTheDocument();
    expect(screen.getByText("0.2.0")).toBeInTheDocument();
    expect(screen.getByText("0.17.16")).toBeInTheDocument();
    expect(screen.getByText(/core/)).toBeInTheDocument();
  });

  it("the community card keeps its plain version", () => {
    render(<EditionCard edition={COMMUNITY} />);
    expect(screen.queryByText(/published by/)).toBeNull();
    expect(screen.getByText("0.17.16")).toBeInTheDocument();
  });

  it("the badge tooltip names the publisher", () => {
    render(<EditionBadge edition="enterprise" publisher="GraalSystems" />);
    expect(screen.getByTestId("edition-badge")).toHaveAttribute(
      "title",
      "Enterprise edition, published by GraalSystems",
    );
  });

  it("the footer says the enterprise edition is by its publisher, built on the core", async () => {
    vi.mocked(api.edition).mockResolvedValue(ENTERPRISE);
    renderFooter();
    expect(await screen.findByText(/Choregos Enterprise edition by GraalSystems/)).toHaveTextContent(
      "Choregos Enterprise edition by GraalSystems · built on Choregos by Varga Foundation (Apache 2.0)",
    );
    expect(screen.getByTestId("footer-foundation")).toBeInTheDocument();
  });

  it("a renamed instance shows its product, the publisher and the foundation in one line", async () => {
    vi.mocked(api.edition).mockResolvedValue(ENTERPRISE);
    renderFooter("Graal Agentic Platform");
    expect(await screen.findByText(/Choregos Enterprise edition by GraalSystems/)).toHaveTextContent(
      "Graal Agentic Platform · Choregos Enterprise edition by GraalSystems · built on Choregos by Varga Foundation (Apache 2.0)",
    );
  });

  it("the footer keeps the foundation's line in the community edition", async () => {
    vi.mocked(api.edition).mockResolvedValue(COMMUNITY);
    renderFooter();
    expect(screen.getByTestId("footer-foundation")).toHaveTextContent("Choregos by Varga Foundation · Apache 2.0");
    await vi.waitFor(() => expect(api.edition).toHaveBeenCalled());
    expect(screen.getByTestId("footer-foundation")).toHaveTextContent("Choregos by Varga Foundation · Apache 2.0");
  });

  it("the footer line follows the product name of the theme the client holds (S42-13)", async () => {
    vi.mocked(api.edition).mockResolvedValue(COMMUNITY);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ServedThemeProvider initial={{ ...DEFAULT_SERVED_THEME, product_name: "Acme Labs" }}>
          <EditionFooterLine />
        </ServedThemeProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("footer-foundation")).toHaveTextContent("Acme Labs, built on Choregos by Varga Foundation");
    await vi.waitFor(() => expect(api.edition).toHaveBeenCalled());
  });

  it("the footer sentence is the theme's, or the console's own without one (S42-13)", () => {
    const { unmount } = render(
      <ServedThemeProvider initial={{ ...DEFAULT_SERVED_THEME, footer: { text: "Operated by Acme Labs" } }}>
        <FooterText />
      </ServedThemeProvider>,
    );
    expect(screen.getByTestId("footer-text")).toHaveTextContent("Operated by Acme Labs");
    unmount();
    render(<FooterText />);
    expect(screen.getByTestId("footer-text")).toHaveTextContent("A ticket goes in, a controlled production release comes out.");
  });
});
