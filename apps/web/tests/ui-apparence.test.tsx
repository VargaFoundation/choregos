import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  Badge,
  Button,
  buttonClasses,
  Card,
  Dot,
  Eyebrow,
  Heading,
  Stat,
  StateBadge,
  TH,
  THead,
  Table,
  TBody,
  TR,
  tabClasses,
} from "@/components/ui";

/**
 * L'allure des primitives de la console (ADR 0043, S24-02) : les mêmes noms que le design system,
 * l'allure d'une console d'opérations. Chaque cas tombe si la primitive reprend celle du design
 * system — le Space Mono gras de 48 px, la puce carrée, l'aplat inversé, le trait d'encre.
 */
describe("les primitives de la console", () => {
  it("un titre de page fait 24 px, léger — plus 48 px gras", () => {
    render(
      <Heading as="h1" size="xl">
        projects
      </Heading>,
    );
    const titre = screen.getByRole("heading", { level: 1, name: "projects" });
    expect(titre).toHaveClass("text-2xl", "font-light");
    expect(titre.className).not.toMatch(/font-bold|text-4xl|text-5xl/);
  });

  it("un en-tête de tableau se pose sur un filet, en petites capitales mono — plus sur un trait d'encre", () => {
    render(
      <Table>
        <THead>
          <TR>
            <TH>state</TH>
          </TR>
        </THead>
        <TBody />
      </Table>,
    );
    const entete = screen.getByRole("columnheader", { name: "state" });
    expect(entete).toHaveClass("font-mono", "uppercase", "h-8");
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

  it("un bouton se commande en mono, 32 px — 44 au doigt —, l'action en iris et non en aplat inversé blanc", () => {
    render(
      <Button tone="primary" onClick={() => {}}>
        approve
      </Button>,
    );
    const bouton = screen.getByRole("button", { name: "approve" });
    expect(bouton).toHaveClass("font-mono", "h-8", "pointer-coarse:min-h-11", "bg-inverse");
    expect(buttonClasses("secondary")).toMatch(/\bbg-raised\b/);
    expect(buttonClasses("danger")).not.toMatch(/hover:bg-danger\b/);
  });

  it("une légende n'a plus de cadre, une carte s'élève, un chiffre est en mono léger", () => {
    render(
      <>
        <Eyebrow>project · billing-api</Eyebrow>
        <Card title="costs">contenu</Card>
        <Stat value="42" label="tickets" />
      </>,
    );
    expect(screen.getByText("project · billing-api")).not.toHaveClass("border");
    expect(screen.getByRole("heading", { name: "costs" }).closest("section")).toHaveClass("raised");
    expect(screen.getByText("42")).toHaveClass("font-mono", "font-light");
  });
});
