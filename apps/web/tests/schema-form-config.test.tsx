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
    company_name: { type: "string", default: "Diametral" },
    hr_contact: { type: "string", description: "Who to refer sensitive matters to" },
    embed_origins: { type: "array", items: { type: "string", format: "uri" }, default: ["https://scorecard.diametral.com"] },
  },
};

function Harness({ schema, start = {} }: { schema: JsonSchema; start?: Record<string, unknown> }) {
  const [value, setValue] = useState<Record<string, unknown>>(start);
  return (
    <>
      <SchemaForm schema={schema} value={value} onChange={setValue} />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}
const value = () => JSON.parse(screen.getByTestId("value").textContent ?? "{}");

describe("a config_schema form", () => {
  it("seeds the defaults and says what is missing", () => {
    expect(defaultValues(COACH)).toEqual({ company_name: "Diametral", embed_origins: ["https://scorecard.diametral.com"] });
    expect(missingFields(COACH, defaultValues(COACH))).toEqual(["hr_contact"]);
  });

  it("lets a second item be typed after a comma, and draws URLs as URL fields", () => {
    render(<Harness schema={COACH} start={defaultValues(COACH)} />);
    const origins = screen.getByLabelText(/embed origins/i);
    expect(origins).toHaveAttribute("type", "url");
    fireEvent.change(origins, { target: { value: "https://scorecard.diametral.com," } });
    expect(origins).toHaveValue("https://scorecard.diametral.com,");
    fireEvent.change(origins, { target: { value: "https://scorecard.diametral.com, https://hr.example.org" } });
    expect(value().embed_origins).toEqual(["https://scorecard.diametral.com", "https://hr.example.org"]);
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
});
