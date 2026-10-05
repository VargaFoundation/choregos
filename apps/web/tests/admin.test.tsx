import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { remplir } from "@/components/admin/section";
import { SchemaForm, champsManquants, type JsonSchema } from "@/components/schema-form";
import { versCsv } from "@/lib/csv";

const schema: JsonSchema = {
  type: "object",
  required: ["name", "role"],
  properties: {
    name: { type: "string", title: "name" },
    role: { type: "string", enum: ["viewer", "developer"] },
    seats: { type: "integer", description: "how many" },
    enabled: { type: "boolean", title: "enabled" },
    domains: { type: "array", items: { type: "string" } },
  },
};

function Formulaire({ onValue }: { onValue: (v: Record<string, unknown>) => void }) {
  const [valeurs, setValeurs] = useState<Record<string, unknown>>({});
  return (
    <SchemaForm
      schema={schema}
      value={valeurs}
      onChange={(v) => {
        setValeurs(v);
        onValue(v);
      }}
    />
  );
}

describe("SchemaForm (ADR 0032, S17-02)", () => {
  it("tire de chaque propriété le contrôle de son type, et marque l'obligatoire", () => {
    render(<Formulaire onValue={() => undefined} />);
    expect(screen.getByLabelText(/^name/)).toHaveAttribute("type", "text");
    expect(screen.getByLabelText(/^role/).tagName).toBe("SELECT");
    expect(screen.getByLabelText(/^seats/)).toHaveAttribute("type", "number");
    expect(screen.getByLabelText(/^enabled/)).toHaveAttribute("type", "checkbox");
    expect(screen.getByLabelText(/^name/).closest("div")?.textContent).toContain("(required)");
  });

  it("rend des valeurs typées, et dit ce qui manque", () => {
    let derniere: Record<string, unknown> = {};
    render(<Formulaire onValue={(v) => (derniere = v)} />);
    fireEvent.change(screen.getByLabelText(/^name/), { target: { value: "okta" } });
    fireEvent.change(screen.getByLabelText(/^seats/), { target: { value: "12" } });
    fireEvent.click(screen.getByLabelText(/^enabled/));
    fireEvent.change(screen.getByLabelText(/^domains/), { target: { value: "varga.dev, diametral.com" } });
    expect(derniere).toEqual({ name: "okta", seats: 12, enabled: true, domains: ["varga.dev", "diametral.com"] });
    expect(champsManquants(schema, derniere)).toEqual(["role"]);
  });
});

describe("les chemins d'une section", () => {
  it("prennent l'organisation et la clé de la ligne, quel que soit le nom du paramètre", () => {
    expect(remplir("/orgs/{org}/scim/tokens/{id}", "varga", "t 1")).toBe("/orgs/varga/scim/tokens/t%201");
    expect(remplir("/orgs/{org}/saml/providers/{provider_id}", "varga", "p1")).toBe("/orgs/varga/saml/providers/p1");
  });
});

describe("l'export CSV de l'audit", () => {
  it("cite ce qui doit l'être, et désarme les formules", () => {
    const csv = versCsv(["a", "b"], [["x,y", 'il a dit "non"'], ["=HYPERLINK(1)", null]]);
    expect(csv).toBe('a,b\r\n"x,y","il a dit ""non"""\r\n\'=HYPERLINK(1),\r\n');
  });
});
