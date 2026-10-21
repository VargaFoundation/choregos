import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SchemaForm, cheminDeTeleversement, refusDuFichier, type JsonSchema } from "@/components/schema-form";

type Upload = (path: string, file: File) => Promise<{ ref: string }>;
let upload: Upload = async () => ({ ref: "" });
const calls: [string, File][] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  // A plain function, not `vi.fn()`: the spy's own bookkeeping of a rejected promise counts as unhandled.
  return { ...real, api: { ...real.api, sectionUpload: (path: string, file: File) => upload(path, file) } };
});

const SCHEMA: JsonSchema = {
  type: "object",
  properties: {
    logo: {
      type: "string",
      format: "asset",
      title: "logo",
      "x-upload": "/orgs/{org}/files",
      "x-accept": ["image/png", "image/svg+xml"],
      "x-max-bytes": 1024,
    },
  },
};

function form(value: Record<string, unknown> = {}, org: string | null = "acme") {
  const onChange = vi.fn();
  render(<SchemaForm schema={SCHEMA} value={value} onChange={onChange} org={org ?? undefined} />);
  return onChange;
}

function pick(file: File) {
  fireEvent.change(screen.getByLabelText("logo"), { target: { files: [file] } });
}

beforeEach(() => {
  calls.length = 0;
  upload = async (path, file) => {
    calls.push([path, file]);
    return { ref: "/api/v1/theme-assets/abc.png" };
  };
});

describe("a schema-form asset field (S42-13)", () => {
  it("offers a file input limited to the accepted types", () => {
    form();
    expect(screen.getByLabelText("logo")).toHaveAttribute("type", "file");
    expect(screen.getByLabelText("logo")).toHaveAttribute("accept", "image/png,image/svg+xml");
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  });

  it("uploads the chosen file to x-upload with {org} filled in and holds the returned ref", async () => {
    const onChange = form();
    const file = new File(["png"], "logo.png", { type: "image/png" });
    pick(file);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ logo: "/api/v1/theme-assets/abc.png" }));
    expect(calls).toEqual([["/orgs/acme/files", file]]);
  });

  it("refuses a wrong type or an oversize file in the browser, without calling the route", () => {
    const onChange = form();
    pick(new File(["gif"], "a.gif", { type: "image/gif" }));
    expect(screen.getByRole("alert")).toHaveTextContent("not accepted");
    pick(new File([new Uint8Array(2048)], "big.png", { type: "image/png" }));
    expect(screen.getByRole("alert")).toHaveTextContent("larger than 1 KiB");
    expect(calls).toEqual([]);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("says what the route refused and keeps the previous value", async () => {
    upload = () => Promise.reject(new Error("not an image"));
    const onChange = form({ logo: "/api/v1/theme-assets/old.png" });
    pick(new File(["png"], "logo.png", { type: "image/png" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeNull());
    expect(screen.getByRole("alert")).toHaveTextContent("not an image");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows a served file as a thumbnail and any other reference as text, never loaded", () => {
    const { unmount } = render(
      <SchemaForm schema={SCHEMA} value={{ logo: "/api/v1/theme-assets/abc.png" }} onChange={vi.fn()} org="acme" />,
    );
    expect(screen.getByAltText("Current file")).toHaveAttribute("src", "/api/v1/theme-assets/abc.png");
    unmount();
    render(<SchemaForm schema={SCHEMA} value={{ logo: "https://evil.example/x.png" }} onChange={vi.fn()} org="acme" />);
    expect(screen.queryByAltText("Current file")).toBeNull();
    expect(screen.getByText("https://evil.example/x.png")).toBeInTheDocument();
  });

  it("removes the file by writing null", () => {
    const onChange = form({ logo: "/api/v1/theme-assets/abc.png" });
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(onChange).toHaveBeenCalledWith({ logo: null });
  });

  it("cannot upload without an organisation to fill {org}", () => {
    form({}, null);
    expect(screen.getByLabelText("logo")).toBeDisabled();
  });
});

describe("the helpers of an asset field", () => {
  it("fills {org} (encoded) and refuses any other placeholder", () => {
    expect(cheminDeTeleversement("/orgs/{org}/x", "a b")).toBe("/orgs/a%20b/x");
    expect(cheminDeTeleversement("/x", undefined)).toBe("/x");
    expect(cheminDeTeleversement("/orgs/{org}/x/{id}", "acme")).toBeNull();
    expect(cheminDeTeleversement(undefined, "acme")).toBeNull();
  });

  it("judges type and size from the schema's limits", () => {
    expect(refusDuFichier({ type: "image/png", size: 10 }, SCHEMA.properties!.logo!)).toBeNull();
    expect(refusDuFichier({ type: "image/png", size: 5000 }, { "x-max-bytes": 100 })).toContain("100 bytes");
    expect(refusDuFichier({ type: "text/html", size: 1 }, { "x-accept": ["image/png"] })).toContain("not accepted");
  });
});
