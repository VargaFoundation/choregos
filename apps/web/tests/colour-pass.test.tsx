import { render, screen } from "@testing-library/react";
import { Activity, Wallet } from "lucide-react";
import { describe, expect, it } from "vitest";
import { Card, Empty, IconTile, Stat } from "@/components/ui";

/**
 * Cards, figures and empty states say what they are about with an icon on its hue (S55-08, ADR 0120),
 * as the page headers and the settings' groups do. Without one, they look as they did.
 */
const tile = (container: HTMLElement) => container.querySelector("[data-icon-tile]") as HTMLElement | null;

describe("the colour pass", () => {
  it("a card with an icon wears it on its hue before its title", () => {
    const { container } = render(
      <Card title="The last 14 days" icon={Wallet} hue={7}>
        content
      </Card>,
    );
    expect(screen.getByRole("heading", { name: "The last 14 days" })).toBeInTheDocument();
    expect(tile(container)!.className).toContain("bg-cat-7-soft");
    expect(tile(container)).toHaveAttribute("aria-hidden", "true");
  });

  it("a card without one is unchanged", () => {
    const { container } = render(<Card title="Plain">content</Card>);
    expect(tile(container)).toBeNull();
  });

  it("a figure carries its icon above its value", () => {
    const { container } = render(<Stat value={3} label="Active tickets" icon={Activity} hue={1} />);
    expect(tile(container)!.className).toContain("bg-cat-1-soft");
    expect(screen.getByText("Active tickets")).toBeInTheDocument();
  });

  it("an empty state shows what is absent", () => {
    const { container } = render(
      <Empty title="No spend" icon={Wallet} hue={7}>
        Nothing yet.
      </Empty>,
    );
    expect(tile(container)!.className).toContain("rounded-full");
  });

  it("a tile without a hue stays neutral", () => {
    const { container } = render(<IconTile icon={Wallet} />);
    expect(tile(container)!.className).toContain("bg-surface-muted");
  });
});
