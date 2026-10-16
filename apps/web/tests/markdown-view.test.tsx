import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownView } from "@/components/markdown-view";

const DOC = "https://learn.example.com/doc";

describe("lists", () => {
  it("renders a numbered list as one list, even when an item wraps and a blank line separates two", () => {
    const { container } = render(
      <MarkdownView text={"1. First step,\n   which wraps.\n\n2. Second step.\n3. Third step."} />,
    );
    const lists = container.querySelectorAll("ol");
    expect(lists).toHaveLength(1);
    const items = [...lists[0]!.querySelectorAll(":scope > li")].map((li) => li.textContent);
    expect(items).toEqual(["First step, which wraps.", "Second step.", "Third step."]);
  });

  it("keeps the number a list starts at", () => {
    const { container } = render(<MarkdownView text={"3. three\n4. four"} />);
    expect(container.querySelector("ol")).toHaveAttribute("start", "3");
  });

  it("nests a bullet list under the item that introduces it", () => {
    const { container } = render(
      <MarkdownView text={"2. Create it. Two kinds exist:\n   - a personal token,\n     sent as Basic;\n   - a service key.\n3. Done."} />,
    );
    const outer = container.querySelector("ol")!;
    expect(outer.querySelectorAll(":scope > li")).toHaveLength(2);
    const nested = [...outer.querySelectorAll(":scope > li:first-child > ul > li")].map((li) => li.textContent);
    expect(nested).toEqual(["a personal token, sent as Basic;", "a service key."]);
  });

  it("starts a new list when the kind changes", () => {
    const { container } = render(<MarkdownView text={"- a\n- b\n1. c"} />);
    expect(container.querySelectorAll("ul")).toHaveLength(1);
    expect(container.querySelectorAll("ol")).toHaveLength(1);
  });
});

describe("links", () => {
  it("links an https address the caller allows, in a new tab, without an opener", () => {
    render(<MarkdownView text={`See [the docs](${DOC}).`} links={[DOC]} />);
    const link = screen.getByRole("link", { name: "the docs" });
    expect(link).toHaveAttribute("href", DOC);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("leaves an address that is not allowed as its label, never as a link", () => {
    render(<MarkdownView text="Go to [elsewhere](https://evil.example/x)." links={[DOC]} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByTestId("markdown-view")).toHaveTextContent("Go to elsewhere.");
  });

  it("never links javascript:, even when it is on the list", () => {
    render(<MarkdownView text="[click](javascript:alert(1))" links={["javascript:alert(1)"]} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByTestId("markdown-view")).toHaveTextContent("click");
  });

  it("never links http, even when it is on the list", () => {
    render(<MarkdownView text="[plain](http://example.com/a)" links={["http://example.com/a"]} />);
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("without an allow-list shows the text as written: an agent's markdown links nothing", () => {
    render(<MarkdownView text={`[the docs](${DOC})`} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByTestId("markdown-view")).toHaveTextContent(`[the docs](${DOC})`);
  });
});
