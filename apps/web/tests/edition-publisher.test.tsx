import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EditionBadge, EditionCard, EditionFooterLine } from "@/components/edition";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, edition: vi.fn() } };
});

const ENTERPRISE = {
  edition: "enterprise" as const,
  features: ["saml"],
  version: "0.17.16",
  publisher: "graal.systems",
  edition_version: "0.2.0",
};
const COMMUNITY = { edition: "community" as const, features: [], version: "0.17.16", publisher: null, edition_version: null };

function renderFooter() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EditionFooterLine />
    </QueryClientProvider>,
  );
}

describe("the edition names its publisher", () => {
  beforeEach(() => vi.mocked(api.edition).mockReset());

  it("the admin card shows the edition version, the publisher and the core version", () => {
    render(<EditionCard edition={ENTERPRISE} />);
    expect(screen.getByText(/published by graal\.systems/)).toBeInTheDocument();
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
    render(<EditionBadge edition="enterprise" publisher="graal.systems" />);
    expect(screen.getByTestId("edition-badge")).toHaveAttribute(
      "title",
      "Enterprise edition, published by graal.systems",
    );
  });

  it("the footer says the enterprise edition is by its publisher, built on the core", async () => {
    vi.mocked(api.edition).mockResolvedValue(ENTERPRISE);
    renderFooter();
    expect(await screen.findByText(/Choregos Enterprise edition by graal\.systems/)).toHaveTextContent(
      "Choregos Enterprise edition by graal.systems · built on Choregos by Varga Foundation (Apache 2.0)",
    );
    expect(screen.getByTestId("footer-foundation")).toBeInTheDocument();
  });

  it("the footer keeps the foundation's line in the community edition", async () => {
    vi.mocked(api.edition).mockResolvedValue(COMMUNITY);
    renderFooter();
    expect(screen.getByTestId("footer-foundation")).toHaveTextContent("Choregos by Varga Foundation · Apache 2.0");
    await vi.waitFor(() => expect(api.edition).toHaveBeenCalled());
    expect(screen.getByTestId("footer-foundation")).toHaveTextContent("Choregos by Varga Foundation · Apache 2.0");
  });
});
