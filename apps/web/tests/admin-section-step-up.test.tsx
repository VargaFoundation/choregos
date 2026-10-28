import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PENDING_ACTION_KEY, SectionRendue } from "@/components/admin/section";
import { api, ApiError } from "@/lib/api";
import type { AdminSection } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, api: { ...original.api, sectionCall: vi.fn() } };
});

const sectionCall = vi.mocked(api.sectionCall);

/** The shape of the enterprise edition's `ee-administrateurs`: an action that needs a fresh sign-in. */
const section = {
  id: "ee-administrateurs",
  title: "Platform administrators",
  scope: "platform",
  permission: "platform:admin",
  blocks: [
    {
      kind: "action",
      label: "add an administrator",
      path: "/plateforme/administrateurs",
      method: "POST",
      confirm: "This person will be able to read and act on every tenant of the instance.",
      reauth: true,
      params: {
        type: "object",
        required: ["email", "motif"],
        properties: { email: { type: "string", title: "e-mail" }, motif: { type: "string", title: "reason" } },
      },
    },
  ],
} as AdminSection;

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SectionRendue section={section} org="acme" />
    </QueryClientProvider>,
  );
}

async function addAdministrator(email: string) {
  fireEvent.click(screen.getByRole("button", { name: "add an administrator" }));
  fireEvent.change(screen.getByLabelText(/^e-mail/), { target: { value: email } });
  fireEvent.change(screen.getByLabelText(/^reason/), { target: { value: "on call" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm add an administrator" }));
}

const stepUp = () => new ApiError(401, { errors: [{ error: "step_up_required" }] });

describe("an action that needs a fresh sign-in survives the trip through the IdP (S42-04b)", () => {
  beforeEach(() => sectionCall.mockReset());

  it("comes back open, filled in, and waits for a new confirmation", async () => {
    sectionCall.mockRejectedValueOnce(stepUp());
    const before = renderSection();
    await addAdministrator("new@acme.test");
    await waitFor(() => expect(sectionCall).toHaveBeenCalledTimes(1));
    before.unmount(); // the browser went to the IdP: the page, and its state, are gone

    renderSection();
    expect(screen.getByLabelText(/^e-mail/)).toHaveValue("new@acme.test");
    expect(screen.getByLabelText(/^reason/)).toHaveValue("on call");
    expect(screen.getByText(/sign-in was refreshed/)).toBeInTheDocument();
    expect(sectionCall).toHaveBeenCalledTimes(1); // never sent on its own
    expect(window.sessionStorage.getItem(PENDING_ACTION_KEY)).toBeNull();

    sectionCall.mockResolvedValueOnce({});
    fireEvent.click(screen.getByRole("button", { name: "Confirm add an administrator" }));
    await waitFor(() => expect(sectionCall).toHaveBeenCalledTimes(2));
    expect(sectionCall).toHaveBeenLastCalledWith("POST", "/plateforme/administrateurs", {
      email: "new@acme.test",
      motif: "on call",
    });
  });

  it("forgets the action once the API has answered anything but a 401", async () => {
    sectionCall.mockRejectedValueOnce(new ApiError(409, { detail: "already an administrator" }));
    const before = renderSection();
    await addAdministrator("new@acme.test");
    expect(await screen.findByText("already an administrator")).toBeInTheDocument();
    before.unmount();

    renderSection();
    expect(screen.queryByLabelText(/^e-mail/)).not.toBeInTheDocument();
  });

  it("forgets the action once it is done", async () => {
    sectionCall.mockResolvedValueOnce({});
    renderSection();
    await addAdministrator("new@acme.test");
    expect(await screen.findByText("add an administrator: done")).toBeInTheDocument();
    expect(window.sessionStorage.getItem(PENDING_ACTION_KEY)).toBeNull();
  });

  it("does not reopen another action, nor an old one", () => {
    const keep = (path: string, at: number) =>
      window.sessionStorage.setItem(
        PENDING_ACTION_KEY,
        JSON.stringify({ method: "POST", path, values: { email: "x@acme.test" }, at }),
      );
    keep("/plateforme/other-chose", Date.now());
    const other = renderSection();
    expect(screen.queryByLabelText(/^e-mail/)).not.toBeInTheDocument();
    other.unmount();

    keep("/plateforme/administrateurs", Date.now() - 60 * 60 * 1000);
    renderSection();
    expect(screen.queryByLabelText(/^e-mail/)).not.toBeInTheDocument();
  });
});
