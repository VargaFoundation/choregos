import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EMPTY_DRAFT, SecretField, bodyOfDrafts, problemOfDraft, stateLine, type SecretDraft } from "@/components/secret-field";
import type { ConnectorSecretState } from "@/lib/types";

const VALUE = "vendor-key-AAAA-1111";
const HOUR = 3_600_000;
const SET: ConnectorSecretState = {
  field: "client_secret",
  source: "store",
  version: 2,
  rotated_at: new Date(Date.now() - 72 * HOUR).toISOString(),
  rotated_by: "lea@acme.dev",
};

describe("a secret field (S39-06d, ADR 0058)", () => {
  it("asks the value as a password, never as readable text", () => {
    const onChange = vi.fn();
    render(<SecretField field="client_secret" draft={EMPTY_DRAFT} onChange={onChange} />);
    const input = screen.getByLabelText("client secret value");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "new-password");
    fireEvent.change(input, { target: { value: VALUE } });
    expect(onChange).toHaveBeenCalledWith({ mode: "value", text: VALUE });
    expect(screen.getByText(/Stored encrypted; it is never shown again/)).toBeInTheDocument();
  });

  it("switches to a reference, which must look like one", () => {
    function Harness() {
      const [draft, setDraft] = useStateForTest();
      return <SecretField field="client_secret" draft={draft} onChange={setDraft} />;
    }
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "Use a reference instead" }));
    const input = screen.getByLabelText("client secret reference");
    expect(input).toHaveAttribute("type", "text");
    fireEvent.change(input, { target: { value: "plain-value" } });
    expect(screen.getByRole("alert")).toHaveTextContent("A reference is expected (env:NAME), not a value");
    fireEvent.change(input, { target: { value: "env:ENTRA_CLIENT_SECRET" } });
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Type the value instead" }));
    expect(screen.getByLabelText("client secret value")).toHaveValue("");
  });

  it("says a secret is set, when and by whom — and never what it is", () => {
    render(<SecretField field="client_secret" state={SET} onSave={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByTestId("secret-state-client_secret")).toHaveTextContent("set · rotated 3 days ago by lea@acme.dev");
    expect(screen.queryByLabelText(/value/)).toBeNull();
    expect(stateLine({ field: "token", source: "reference", reference: "env:SUPPLIER_TOKEN" })).toBe("reference env:SUPPLIER_TOKEN");
    expect(stateLine({ field: "token", source: "store", version: null })).toBe("stored value missing: type it again");
  });

  it("replaces a secret with an empty input, sends it once, and the value is gone from the page", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<SecretField field="client_secret" state={SET} onSave={onSave} onClear={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    const input = screen.getByLabelText("client secret value");
    expect(input).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.change(input, { target: { value: VALUE } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({ mode: "value", text: VALUE }));
    await waitFor(() => expect(screen.queryByLabelText("client secret value")).toBeNull());
    expect(document.body.innerHTML).not.toContain(VALUE);
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument();
  });

  it("clears a secret, and shows a refusal without keeping the typed value", async () => {
    const onClear = vi.fn().mockRejectedValue(new Error("forbidden: org admin only"));
    render(<SecretField field="client_secret" state={SET} onSave={vi.fn()} onClear={onClear} />);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(await screen.findByText(/forbidden: org admin only/)).toBeInTheDocument();
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("offers a first value for a field never set, with no Clear", () => {
    render(<SecretField field="client_secret" onSave={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByTestId("secret-state-client_secret")).toHaveTextContent("not set");
    expect(screen.getByRole("button", { name: "Set" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  });

  it("turns drafts into the body: values to seal, references to keep, nothing for an empty one", () => {
    const drafts: Record<string, SecretDraft> = {
      client_secret: { mode: "value", text: VALUE },
      token: { mode: "reference", text: "env:SUPPLIER_TOKEN" },
      empty: { mode: "value", text: "" },
    };
    expect(bodyOfDrafts(drafts)).toEqual({
      secret_values: { client_secret: VALUE },
      secret_refs: { token: "env:SUPPLIER_TOKEN" },
    });
    expect(problemOfDraft({ mode: "reference", text: "nope" })).not.toBeNull();
    expect(problemOfDraft({ mode: "value", text: "anything: goes" })).toBeNull();
  });
});

import { useState } from "react";
function useStateForTest() {
  return useState<SecretDraft>(EMPTY_DRAFT);
}
