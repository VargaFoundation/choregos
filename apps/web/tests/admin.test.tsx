import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { remplir } from "@/components/admin/section";
import { SchemaForm, champsManquants, lireUneCorrespondance, type JsonSchema } from "@/components/schema-form";
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

/** Ce que demande le SAML de l'édition entreprise (S17-04) : un certificat, une correspondance. */
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

describe("SchemaForm : textes sur plusieurs lignes et correspondances (S17-04)", () => {
  const PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----";

  it("un certificat garde ses fins de ligne : une ligne de saisie les aurait mangées", () => {
    let derniere: Record<string, unknown> = {};
    render(<FormulaireSaml initial={{}} onValue={(v) => (derniere = v)} />);
    const certificat = screen.getByLabelText(/^certificate/);
    expect(certificat.tagName).toBe("TEXTAREA");
    fireEvent.change(certificat, { target: { value: PEM } });
    expect(derniere.certificat_pem).toBe(PEM);
  });

  it("une correspondance se tape une paire par ligne, et se relit telle qu'elle a été écrite", () => {
    let derniere: Record<string, unknown> = {};
    render(<FormulaireSaml initial={{ correspondance: { admins: "org_admin" } }} onValue={(v) => (derniere = v)} />);
    const correspondance = screen.getByLabelText(/^mapping/);
    expect(correspondance).toHaveValue("admins = org_admin");
    fireEvent.change(correspondance, { target: { value: "admins = org_admin\ndevs=developer\n" } });
    expect(derniere.correspondance).toEqual({ admins: "org_admin", devs: "developer" });
  });

  it("une ligne illisible est dite, pas effacée ; une valeur lue après coup remplace le texte", () => {
    let derniere: Record<string, unknown> = {};
    render(<FormulaireSaml initial={{}} onValue={(v) => (derniere = v)} />);
    const correspondance = screen.getByLabelText(/^mapping/);
    fireEvent.change(correspondance, { target: { value: "admins = org_admin\nsans egal" } });
    expect(screen.getByRole("alert")).toHaveTextContent("line 2: key = value expected");
    expect(correspondance).toHaveValue("admins = org_admin\nsans egal");
    expect(derniere.correspondance).toEqual({ admins: "org_admin" });

    fireEvent.click(screen.getByRole("button", { name: "lire" }));
    expect(screen.getByLabelText(/^mapping/)).toHaveValue("lus = viewer");
  });

  it("une correspondance vide manque, si elle est exigée", () => {
    expect(champsManquants({ required: ["m"], properties: { m: { type: "object" } } }, { m: {} })).toEqual(["m"]);
    expect(lireUneCorrespondance(" = x\na = b = c").valeurs).toEqual({ a: "b = c" });
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
