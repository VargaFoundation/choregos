import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { SchemaForm, defaultValues, listItem, missingFields, type JsonSchema } from "@/components/schema-form";

// The `config_schema` of the specification's first assistant (spec B): a string with a default, a described
// string, a list of URLs with a default; and the other shapes a schema of an assistant may take.
const COACH: JsonSchema = {
  type: "object",
  required: ["company_name", "hr_contact"],
  properties: {
    company_name: { type: "string", default: "Globex" },
    hr_contact: { type: "string", description: "Who to refer sensitive matters to" },
    embed_origins: { type: "array", items: { type: "string", format: "uri" }, default: ["https://scorecard.example.com"] },
  },
};

function Harness({ schema, start = {} }: { schema: JsonSchema; start?: Record<string, unknown> }) {
  const [value, setValue] = useState<Record<string, unknown>>(start);
  return (
    <>
      <SchemaForm schema={schema} value={value} onChange={setValue} nestedObjects />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}
const value = () => JSON.parse(screen.getByTestId("value").textContent ?? "{}");

describe("a config_schema form", () => {
  it("seeds the defaults and says what is missing", () => {
    expect(defaultValues(COACH)).toEqual({ company_name: "Globex", embed_origins: ["https://scorecard.example.com"] });
    expect(missingFields(COACH, defaultValues(COACH))).toEqual(["hr_contact"]);
  });

  it("lets a second item be typed after a comma, and draws URLs as URL fields", () => {
    render(<Harness schema={COACH} start={defaultValues(COACH)} />);
    const origins = screen.getByLabelText(/embed origins/i);
    expect(origins).toHaveAttribute("type", "url");
    fireEvent.change(origins, { target: { value: "https://scorecard.example.com," } });
    expect(origins).toHaveValue("https://scorecard.example.com,");
    fireEvent.change(origins, { target: { value: "https://scorecard.example.com, https://hr.example.org" } });
    expect(value().embed_origins).toEqual(["https://scorecard.example.com", "https://hr.example.org"]);
  });

  it("types the items of a list of numbers", () => {
    expect(listItem("3", { type: "integer" })).toBe(3);
    render(<Harness schema={{ type: "object", properties: { days: { type: "array", items: { type: "integer" } } } }} />);
    fireEvent.change(screen.getByLabelText("days"), { target: { value: "7, 30" } });
    expect(value().days).toEqual([7, 30]);
  });

  it("draws a closed list as checkboxes", () => {
    render(<Harness schema={{ type: "object", properties: { channels: { type: "array", items: { enum: ["inbox", "email", "teams"] } } } }} />);
    fireEvent.click(screen.getByLabelText("teams"));
    fireEvent.click(screen.getByLabelText("inbox"));
    expect(value().channels).toEqual(["inbox", "teams"]);
    fireEvent.click(screen.getByLabelText("teams"));
    expect(value().channels).toEqual(["inbox"]);
  });

  it("draws the fields of a fixed object, not a key = value mapping", () => {
    render(
      <Harness
        schema={{
          type: "object",
          properties: { limits: { type: "object", properties: { per_day: { type: "integer" } } } },
        }}
      />,
    );
    fireEvent.change(screen.getByLabelText("per day"), { target: { value: "5" } });
    expect(value()).toEqual({ limits: { per_day: 5 } });
  });

  it("draws a oneOf of labelled constants as a choice that keeps the constant's own type", () => {
    render(
      <Harness
        schema={{
          type: "object",
          properties: {
            tone: { oneOf: [{ const: "formal", title: "Formal" }, { const: "casual", title: "Casual" }] },
            level: { anyOf: [{ const: 1 }, { const: 2 }] },
          },
        }}
      />,
    );
    fireEvent.change(screen.getByLabelText("tone"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("level"), { target: { value: "1" } });
    expect(value()).toEqual({ tone: "casual", level: 2 });
    expect(screen.getByRole("option", { name: "Formal" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("tone"), { target: { value: "" } });
    expect(value()).toEqual({ level: 2 });
  });

  it("draws a nullable scalar as that scalar, and a mixed one as text typed on the way out", () => {
    render(
      <Harness
        schema={{
          type: "object",
          properties: {
            days: { anyOf: [{ type: "integer" }, { type: "null" }] },
            limit: { oneOf: [{ type: "integer" }, { type: "string" }] },
          },
        }}
      />,
    );
    const days = screen.getByLabelText("days");
    expect(days).toHaveAttribute("type", "number");
    fireEvent.change(days, { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("limit"), { target: { value: "30" } });
    expect(value()).toEqual({ days: 7, limit: 30 });
    fireEvent.change(screen.getByLabelText("limit"), { target: { value: "unlimited" } });
    expect(value().limit).toBe("unlimited");
  });

  it("follows a local $ref for a field, a list's items and a fixed object", () => {
    const schema: JsonSchema = {
      type: "object",
      $defs: {
        window: { type: "object", properties: { from: { type: "string" }, to: { type: "string" } } },
        channel: { enum: ["inbox", "email"] },
      },
      properties: {
        hours: { $ref: "#/$defs/window" },
        channels: { type: "array", items: { $ref: "#/$defs/channel" } },
      },
    };
    render(<Harness schema={schema} />);
    fireEvent.change(screen.getByLabelText("from"), { target: { value: "09:00" } });
    fireEvent.click(screen.getByLabelText("email"));
    expect(value()).toEqual({ hours: { from: "09:00" }, channels: ["email"] });
  });

  it("does not loop on a $ref to itself, nor fail on a dangling one", () => {
    const schema: JsonSchema = {
      type: "object",
      $defs: { node: { type: "object", properties: { name: { type: "string" }, child: { $ref: "#/$defs/node" } } } },
      properties: { tree: { $ref: "#/$defs/node" }, lost: { $ref: "#/$defs/missing", type: "string" } },
    };
    render(<Harness schema={schema} />);
    expect(screen.getAllByLabelText("name").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("lost")).toBeInTheDocument();
  });

  it("draws a list of objects: add, edit, remove, and nested lists of objects inside", () => {
    const schema: JsonSchema = {
      type: "object",
      properties: {
        sources: {
          type: "array",
          items: {
            type: "object",
            properties: {
              name: { type: "string" },
              feeds: { type: "array", items: { type: "object", properties: { url: { type: "string", format: "uri" } } } },
            },
          },
        },
      },
    };
    render(<Harness schema={schema} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an item" }));
    fireEvent.change(screen.getByLabelText("name"), { target: { value: "Reuters" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Add an item" })[0]!);
    fireEvent.change(screen.getByLabelText("url"), { target: { value: "https://r.example/rss" } });
    expect(value()).toEqual({ sources: [{ name: "Reuters", feeds: [{ url: "https://r.example/rss" }] }] });
    fireEvent.click(screen.getAllByRole("button", { name: "Add an item" }).at(-1)!);
    expect(value().sources).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Remove item 2" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Remove item 1" }).at(-1)!); // the outer item's, after its feeds'
    expect(value()).toEqual({});
  });
});
