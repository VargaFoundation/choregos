import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GeneralSection } from "@/components/project-settings/general-section";

/**
 * A save re-seeds the form from the saved project (it is keyed on its data). The "saved" line lives
 * above the form, so it survives the remount: before, it vanished as soon as the refetch landed and
 * the e2e readiness walk missed it under load (integration of main 0.18.8).
 */
const saved = { current: false };
const base = {
  id: "p",
  slug: "dev",
  org: "acme",
  name: "Dev",
  status: "active",
  template_ref: null,
  config: { slug: "dev", org: "acme", repo: { url: "https://github.com/acme/box.git", default_branch: "main" } },
};

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      project: vi.fn(async () =>
        saved.current
          ? { ...base, config: { ...base.config, repo: { ...base.config.repo, test_command: "pytest -q" } } }
          : structuredClone(base),
      ),
      updateProject: vi.fn(async () => {
        saved.current = true;
        return base;
      }),
      templates: vi.fn(async () => []),
    },
  };
});

describe("a settings section after a save", () => {
  it("keeps saying it saved once the form is re-seeded with the saved data", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <GeneralSection slug="dev" />
      </QueryClientProvider>,
    );
    fireEvent.change(await screen.findByLabelText("Test command"), { target: { value: "pytest -q" } });
    fireEvent.click(screen.getByRole("button", { name: "Save general settings" }));
    // The re-seeded form shows the saved value, and the line is still there.
    await waitFor(() => expect(screen.getByLabelText("Test command")).toHaveValue("pytest -q"));
    await waitFor(() => expect(client.isFetching()).toBe(0));
    const section = screen.getByRole("button", { name: "Save general settings" }).closest("form")!.parentElement!;
    expect(within(section).getByRole("status")).toHaveTextContent("General settings saved");
  });
});
