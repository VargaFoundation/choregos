import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  Alert,
  Badge,
  Button,
  buttonClasses,
  Card,
  CHAMP,
  Checkbox,
  Code,
  Dot,
  Eyebrow,
  Heading,
  LEGENDE,
  Stat,
  StateBadge,
  StepNumber,
  TH,
  THead,
  Table,
  TBody,
  TR,
  tabClasses,
  Wordmark,
} from "@/components/ui";

/**
 * L'allure des primitives de la console (ADR 0043, S24-02, then ADR 0048): the console owns them, and
 * they look like an operations console in the manner of Temporal's UI — sans for what is read, mono
 * for what is compared, 4–6 px corners, round pills. Each case falls if a primitive takes back the
 * foundation's look: 48 px bold Space Mono, the square dot, the inverted slab, the ink rule, mono
 * buttons, uppercase captions, square corners.
 */
describe("les primitives de la console", () => {
  it("a page title is 24 px, semibold, sans — no longer 48 px of bold display mono", () => {
    render(
      <Heading as="h1" size="xl">
        Projects
      </Heading>,
    );
    const titre = screen.getByRole("heading", { level: 1, name: "Projects" });
    expect(titre).toHaveClass("text-2xl", "font-semibold");
    expect(titre.className).not.toMatch(/font-bold|font-mono|font-display|text-4xl|text-5xl/);
  });

  it("a table header sits on a hairline, in a small sans caption — no ink rule, no uppercase mono", () => {
    render(
      <Table>
        <THead>
          <TR>
            <TH>State</TH>
          </TR>
        </THead>
        <TBody />
      </Table>,
    );
    const entete = screen.getByRole("columnheader", { name: "State" });
    expect(entete).toHaveClass("h-8", "text-xs");
    expect(entete.className).not.toMatch(/\b(font-mono|uppercase)\b/);
    expect(LEGENDE).not.toMatch(/\b(font-mono|uppercase)\b/);
    expect(entete.closest("thead")).toHaveClass("border-line");
    expect(entete.closest("thead")?.className).not.toMatch(/border-ink/);
  });

  it("l'onglet actif porte l'accent, plus l'encre", () => {
    expect(tabClasses(true)).toMatch(/\bborder-accent\b/);
    expect(tabClasses(true)).not.toMatch(/border-ink/);
    expect(tabClasses(false)).toMatch(/\bborder-transparent\b/);
  });

  it("un état qui attend quelqu'un est une pastille ambre, plus un carré blanc", () => {
    render(<StateBadge state="awaiting_spec_approval" display="Spec to approve" kind="wait" />);
    const pastille = screen.getByText("Spec to approve").closest("span");
    expect(pastille).toHaveClass("rounded-full", "bg-waiting-soft", "text-waiting-ink");
  });

  it("un état qui tourne est bleu, un état bloqué rouge, un état final vert", () => {
    render(
      <>
        <StateBadge state="in_progress" display="In progress" kind="work" />
        <StateBadge state="needs_human" display="Needs a human" />
        <StateBadge state="done" display="Done" kind="terminal" />
      </>,
    );
    expect(screen.getByText("In progress").closest("span")).toHaveClass("bg-running-soft");
    expect(screen.getByText("Needs a human").closest("span")).toHaveClass("bg-failed-soft");
    expect(screen.getByText("Done").closest("span")).toHaveClass("bg-succeeded-soft");
  });

  it("une pastille et son point sont ronds", () => {
    const { container } = render(
      <>
        <Badge tone="ok">ok</Badge>
        <Dot tone="agent" />
      </>,
    );
    expect(screen.getByText("ok").closest("span")).toHaveClass("rounded-full");
    expect(container.querySelectorAll(".rounded-full").length).toBeGreaterThanOrEqual(3);
  });

  it("a button is sans with 4 px corners, 32 px — 44 to the finger —, the action in iris and not an inverted white slab", () => {
    render(
      <Button tone="primary" onClick={() => {}}>
        Approve
      </Button>,
    );
    const bouton = screen.getByRole("button", { name: "Approve" });
    expect(bouton).toHaveClass("rounded", "h-8", "pointer-coarse:min-h-11", "bg-inverse");
    expect(bouton.className).not.toMatch(/\bfont-mono\b/);
    expect(CHAMP).toMatch(/\brounded\b/);
    expect(buttonClasses("secondary")).toMatch(/\bbg-raised\b/);
    expect(buttonClasses("danger")).not.toMatch(/hover:bg-danger\b/);
  });

  it("a caption has no frame, a card is raised with 6 px corners, a number is in light mono", () => {
    render(
      <>
        <Eyebrow>Project · billing-api</Eyebrow>
        <Card title="Costs">contenu</Card>
        <Stat value="42" label="Tickets" />
      </>,
    );
    expect(screen.getByText("Project · billing-api")).not.toHaveClass("border");
    const card = screen.getByRole("heading", { level: 2, name: "Costs" }).closest("section");
    expect(card).toHaveClass("raised", "rounded-md", "border-line");
    expect(screen.getByText("42")).toHaveClass("font-mono", "font-light");
  });

  it("the console's own alert, code, checkbox and step number replace the foundation's", () => {
    const { container } = render(
      <>
        <Alert tone="warn" title="Not reachable from here yet">
          The platform has no public address.
        </Alert>
        <Alert tone="danger">Could not read the costs.</Alert>
        <Code>mcp:write</Code>
        <Checkbox checked />
        <Checkbox checked={false} />
        <StepNumber value={2} />
      </>,
    );
    expect(screen.getByRole("status")).toHaveClass("rounded-md", "bg-waiting-soft", "text-waiting-ink");
    expect(screen.getByRole("alert")).toHaveClass("rounded-md", "bg-failed-soft");
    expect(screen.getByText("mcp:write").tagName).toBe("CODE");
    expect(screen.getByText("mcp:write")).toHaveClass("font-mono", "rounded-sm");
    const boxes = container.querySelectorAll("span[aria-hidden].size-4");
    expect(boxes).toHaveLength(2);
    expect(boxes[0]).toHaveClass("rounded-sm", "bg-accent");
    expect(boxes[0]!.querySelector("svg")).not.toBeNull();
    expect(boxes[1]!.querySelector("svg")).toBeNull();
    // A step is numbered `2`, not `02` on a black square.
    expect(screen.getByText("2")).toHaveClass("rounded-full", "font-mono");
  });

  it("the mark is the Choregos wordmark, not the foundation's", () => {
    const { container } = render(<Wordmark />);
    expect(container).toHaveTextContent(/^Choregos$/);
    expect(container.textContent).not.toMatch(/varga/i);
  });
});
