import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ServedThemeProvider } from "@/components/served-theme";
import { Wordmark } from "@/components/ui";
import { DEFAULT_SERVED_THEME } from "@/lib/served-theme";
import { SAMPLE_THEME } from "@/mocks/theme-sample";

describe("the wordmark follows the served theme (S39-03b)", () => {
  it("is the console's own, in the default theme and without a provider", () => {
    const { container } = render(<Wordmark />);
    expect(screen.getByText("Choregos")).toBeTruthy();
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows the theme's logo and product name", () => {
    const { container } = render(
      <ServedThemeProvider initial={{ ...SAMPLE_THEME, logo_dark: "/api/v1/dark.svg" }}>
        <Wordmark />
      </ServedThemeProvider>,
    );
    expect(screen.getByText("Sample")).toBeTruthy();
    expect(container.querySelector("svg")).toBeNull();
    expect(screen.getByTestId("wordmark-logo-light").getAttribute("src")).toBe(SAMPLE_THEME.logo);
    expect(screen.getByTestId("wordmark-logo-dark").getAttribute("src")).toBe("/api/v1/dark.svg");
  });

  it("serves one logo to both modes when the theme has one", () => {
    render(
      <ServedThemeProvider initial={SAMPLE_THEME}>
        <Wordmark />
      </ServedThemeProvider>,
    );
    expect(screen.getByTestId("wordmark-logo-dark").getAttribute("src")).toBe(SAMPLE_THEME.logo);
  });

  it("takes only a name from a theme without a logo, and hides it when compact", () => {
    render(
      <ServedThemeProvider initial={{ ...DEFAULT_SERVED_THEME, product_name: "Acme" }}>
        <Wordmark compact />
      </ServedThemeProvider>,
    );
    expect(screen.getByText("Acme").className).toContain("sr-only");
  });

  it("cuts a long name and says it whole in a tooltip (S42-13)", () => {
    const long = "A very long organisation product name!!!";
    render(
      <ServedThemeProvider initial={{ ...DEFAULT_SERVED_THEME, product_name: long }}>
        <Wordmark />
      </ServedThemeProvider>,
    );
    const name = screen.getByText(long);
    expect(name.className).toContain("truncate");
    expect(name).toHaveAttribute("title", long);
  });
});
