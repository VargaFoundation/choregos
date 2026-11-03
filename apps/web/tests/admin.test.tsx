import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { remplir } from "@/components/admin/section";
import { SchemaForm, missingFields, parseMapping, type JsonSchema } from "@/components/schema-form";
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
  it("derives from each property the control of its type, and marks the required one", () => {
    render(<Formulaire onValue={() => undefined} />);
    expect(screen.getByLabelText(/^name/)).toHaveAttribute("type", "text");
    expect(screen.getByLabelText(/^role/).tagName).toBe("SELECT");
    expect(screen.getByLabelText(/^seats/)).toHaveAttribute("type", "number");
    expect(screen.getByLabelText(/^enabled/)).toHaveAttribute("type", "checkbox");
    expect(screen.getByLabelText(/^name/).closest("div")?.textContent).toContain("(required)");
  });

  it("renders typed values, and says what is missing", () => {
    let latest: Record<string, unknown> = {};
    render(<Formulaire onValue={(v) => (latest = v)} />);
    fireEvent.change(screen.getByLabelText(/^name/), { target: { value: "okta" } });
    fireEvent.change(screen.getByLabelText(/^seats/), { target: { value: "12" } });
    fireEvent.click(screen.getByLabelText(/^enabled/));
    fireEvent.change(screen.getByLabelText(/^domains/), { target: { value: "acme.dev, diametral.com" } });
    expect(latest).toEqual({ name: "okta", seats: 12, enabled: true, domains: ["acme.dev", "diametral.com"] });
    expect(missingFields(schema, latest)).toEqual(["role"]);
  });
});

/** What the enterprise edition's SAML asks for (S17-04): a certificate, a mapping. */
const saml: JsonSchema = {
  type: "object",
  required: ["certificat_pem"],
  properties: {
    certificat_pem: { type: "string", format: "multiline", title: "certificate" },
    correspondance: { type: "object", additionalProperties: { type: "string" }, title: "mapping" },
  },
};

function FormulaireSaml({ initial, onValue }: { initial: Record<string, unknown>; onValue: (v: Record<string, unknown>) => void }) {
  const [valeurs, setValeurs] = useState<Record<string, unknown>>(initial);
  return (
    <>
      <button type="button" onClick={() => setValeurs({ correspondance: { lus: "viewer" } })}>
        lire
      </button>
      <SchemaForm
        schema={saml}
        value={valeurs}
        onChange={(v) => {
          setValeurs(v);
          onValue(v);
        }}
      />
    </>
  );
}

describe("SchemaForm: multi-line texts and mappings (S17-04)", () => {
  const PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----";

  it("a certificate keeps its line endings: a one-line input would have eaten them", () => {
    let latest: Record<string, unknown> = {};
    render(<FormulaireSaml initial={{}} onValue={(v) => (latest = v)} />);
    const certificat = screen.getByLabelText(/^certificate/);
    expect(certificat.tagName).toBe("TEXTAREA");
    fireEvent.change(certificat, { target: { value: PEM } });
    expect(latest.certificat_pem).toBe(PEM);
  });

  it("a mapping is typed one pair per line, and re-read as it was written", () => {
    let latest: Record<string, unknown> = {};
    render(<FormulaireSaml initial={{ correspondance: { admins: "org_admin" } }} onValue={(v) => (latest = v)} />);
    const correspondance = screen.getByLabelText(/^mapping/);
    expect(correspondance).toHaveValue("admins = org_admin");
    fireEvent.change(correspondance, { target: { value: "admins = org_admin\ndevs=developer\n" } });
    expect(latest.correspondance).toEqual({ admins: "org_admin", devs: "developer" });
  });

  it("an unreadable line is reported, not erased; a value read afterwards replaces the text", () => {
    let latest: Record<string, unknown> = {};
    render(<FormulaireSaml initial={{}} onValue={(v) => (latest = v)} />);
    const correspondance = screen.getByLabelText(/^mapping/);
    fireEvent.change(correspondance, { target: { value: "admins = org_admin\nsans egal" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Line 2: key = value expected");
    expect(correspondance).toHaveValue("admins = org_admin\nsans egal");
    expect(latest.correspondance).toEqual({ admins: "org_admin" });

    fireEvent.click(screen.getByRole("button", { name: "lire" }));
    expect(screen.getByLabelText(/^mapping/)).toHaveValue("lus = viewer");
  });

  it("an empty mapping is missing, if it is required", () => {
    expect(missingFields({ required: ["m"], properties: { m: { type: "object" } } }, { m: {} })).toEqual(["m"]);
    expect(parseMapping(" = x\na = b = c").valeurs).toEqual({ a: "b = c" });
  });
});

describe("a section's paths", () => {
  it("take the organisation and the row's key, whatever the parameter's name", () => {
    expect(remplir("/orgs/{org}/scim/tokens/{id}", "acme", "t 1")).toBe("/orgs/acme/scim/tokens/t%201");
    expect(remplir("/orgs/{org}/saml/providers/{provider_id}", "acme", "p1")).toBe("/orgs/acme/saml/providers/p1");
  });
});

describe("the audit CSV export", () => {
  it("quotes what has to be, and disarms formulas", () => {
    const csv = versCsv(["a", "b"], [["x,y", 'il a dit "non"'], ["=HYPERLINK(1)", null]]);
    expect(csv).toBe('a,b\r\n"x,y","il a dit ""non"""\r\n\'=HYPERLINK(1),\r\n');
  });
});
