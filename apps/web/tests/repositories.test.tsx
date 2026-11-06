import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GeneralSection, repositoriesProblem } from "@/components/project-settings/general-section";
import { TargetRepository } from "@/components/ticket/target-repository";
import { api } from "@/lib/api";
import { nameOfUrl, primaryName, repositoryNames } from "@/lib/repositories";

/**
 * A project's repositories and a ticket's target (S58-03, ADR 0124): the settings list them, add, remove
 * and swap the primary; a ticket says which one it works on and can be pointed at another while no step
 * has touched it.
 */
const config = {
  slug: "platform",
  org: "acme",
  repo: { url: "https://github.com/acme/infra.git", default_branch: "main", test_command: "make test" },
  repositories: [{ name: "diamctl", url: "https://github.com/acme/diamctl.git", default_branch: "main", branch_prefix: "fix/" }],
};
const project = { id: "p", slug: "platform", org: "acme", name: "Platform", status: "active", template_ref: null, config };
const developer = { id: "u1", email: "lea@acme.dev", display_name: "Léa", memberships: [{ org: "acme", role: "developer" }] };

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: {
      ...real.api,
      project: vi.fn(),
      updateProject: vi.fn(),
      templates: vi.fn(async () => []),
      me: vi.fn(),
      updateWorkItem: vi.fn(),
    },
  };
});

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>);
}

beforeEach(() => {
  vi.mocked(api.project).mockResolvedValue(structuredClone(project) as never);
  vi.mocked(api.updateProject).mockReset().mockResolvedValue(project as never);
  vi.mocked(api.me).mockResolvedValue(developer as never);
  vi.mocked(api.updateWorkItem).mockReset().mockResolvedValue({} as never);
});

describe("a repository's name", () => {
  it("is the one the API derives from its URL", () => {
    expect(nameOfUrl("https://github.com/acme/Infra.git")).toBe("infra");
    expect(nameOfUrl("git@github.com:acme/choregos_deploy.git")).toBe("choregos-deploy");
    expect(nameOfUrl("https://github.com/acme/..git")).toBe("primary");
    expect(primaryName(config as never)).toBe("infra");
    expect(repositoryNames(config as never)).toEqual(["infra", "diamctl"]);
    expect(repositoryNames({ slug: "x", org: "y" } as never)).toEqual([]);
  });

  it("is refused before the save when the API would refuse it", () => {
    const row = { name: "diamctl", url: "https://x/y.git", branch: "main", test: "", lint: "", typecheck: "", rest: {} };
    expect(repositoriesProblem("infra", [row])).toBeNull();
    expect(repositoriesProblem("infra", [{ ...row, name: "infra" }])).toMatch(/Two repositories are named infra/);
    expect(repositoriesProblem("infra", [{ ...row, name: "Diam ctl" }])).toMatch(/needs a name/);
    expect(repositoriesProblem("infra", [{ ...row, url: " " }])).toMatch(/needs its URL/);
    expect(repositoriesProblem(null, [row])).toMatch(/primary/);
  });
});

describe("the project's repositories in its settings", () => {
  it("adds one and saves it with the others, the primary kept", async () => {
    wrap(<GeneralSection slug="platform" />);
    const list = await screen.findByRole("list", { name: "Other repositories" });
    expect(within(list).getByLabelText("Repository name 1")).toHaveValue("diamctl");
    fireEvent.click(screen.getByRole("button", { name: "Add a repository" }));
    fireEvent.change(screen.getByLabelText("Repository name 2"), { target: { value: "fondation" } });
    fireEvent.change(screen.getByLabelText("Repository URL 2"), { target: { value: "https://github.com/acme/fondation.git" } });
    fireEvent.change(screen.getByLabelText("Test command 2"), { target: { value: "make check" } });
    fireEvent.click(screen.getByRole("button", { name: "Save general settings" }));
    await waitFor(() => expect(api.updateProject).toHaveBeenCalled());
    const sent = vi.mocked(api.updateProject).mock.calls[0]![1].config as typeof config;
    expect(sent.repo.url).toBe("https://github.com/acme/infra.git");
    expect(sent.repositories).toEqual([
      expect.objectContaining({ name: "diamctl", url: "https://github.com/acme/diamctl.git", branch_prefix: "fix/" }),
      expect.objectContaining({ name: "fondation", url: "https://github.com/acme/fondation.git", test_command: "make check" }),
    ]);
  });

  it("refuses a name another repository holds, and removes one", async () => {
    wrap(<GeneralSection slug="platform" />);
    fireEvent.change(await screen.findByLabelText("Repository name 1"), { target: { value: "infra" } });
    expect(screen.getByText(/Two repositories are named infra/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save general settings" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Remove infra" }));
    fireEvent.click(screen.getByRole("button", { name: "Save general settings" }));
    await waitFor(() => expect(api.updateProject).toHaveBeenCalled());
    expect(vi.mocked(api.updateProject).mock.calls[0]![1].config).not.toHaveProperty("repositories");
  });

  it("makes another repository the primary, and the primary joins the list under its name", async () => {
    wrap(<GeneralSection slug="platform" />);
    fireEvent.click(await screen.findByRole("button", { name: "Make diamctl the primary" }));
    expect(screen.getByLabelText("Repository URL")).toHaveValue("https://github.com/acme/diamctl.git");
    expect(screen.getByLabelText("Name in the project")).toHaveValue("diamctl");
    expect(screen.getByLabelText("Repository name 1")).toHaveValue("infra");
    fireEvent.click(screen.getByRole("button", { name: "Save general settings" }));
    await waitFor(() => expect(api.updateProject).toHaveBeenCalled());
    const sent = vi.mocked(api.updateProject).mock.calls[0]![1].config as typeof config & { repo: { name?: string; branch_prefix?: string } };
    expect(sent.repo).toEqual(expect.objectContaining({ name: "diamctl", url: "https://github.com/acme/diamctl.git", branch_prefix: "fix/" }));
    expect(sent.repositories).toEqual([
      expect.objectContaining({ name: "infra", url: "https://github.com/acme/infra.git", test_command: "make test" }),
    ]);
  });
});

describe("a ticket's repository", () => {
  const item = { id: "w1", repository: "diamctl", repository_fixed: false, closed_at: null };

  it("is shown, and set to another one while no step has touched it", async () => {
    const onDone = vi.fn();
    wrap(
      <dl>
        <TargetRepository slug="platform" item={item} onDone={onDone} />
      </dl>,
    );
    const choice = await screen.findByLabelText("Repository");
    expect(choice).toHaveValue("diamctl");
    expect(within(choice).getByRole("option", { name: "infra (primary)" })).toBeInTheDocument();
    fireEvent.change(choice, { target: { value: "infra" } });
    fireEvent.click(screen.getByRole("button", { name: "Set the repository" }));
    await waitFor(() => expect(api.updateWorkItem).toHaveBeenCalledWith("w1", { repository: "infra" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("is only said once fixed", async () => {
    wrap(
      <dl>
        <TargetRepository slug="platform" item={{ ...item, repository_fixed: true }} onDone={vi.fn()} />
      </dl>,
    );
    const facts = await screen.findByTestId("ticket-repository");
    await waitFor(() => expect(facts).toHaveTextContent("Fixed: a step already worked on it."));
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("shows the API's refusal", async () => {
    vi.mocked(api.updateWorkItem).mockRejectedValue(new Error("the work item works on `diamctl` since its first step"));
    wrap(
      <dl>
        <TargetRepository slug="platform" item={item} onDone={vi.fn()} />
      </dl>,
    );
    fireEvent.change(await screen.findByLabelText("Repository"), { target: { value: "infra" } });
    fireEvent.click(screen.getByRole("button", { name: "Set the repository" }));
    expect(await screen.findByText(/since its first step/)).toBeInTheDocument();
  });

  it("is not offered to a viewer", async () => {
    vi.mocked(api.me).mockResolvedValue({ ...developer, memberships: [{ org: "acme", role: "viewer" }] } as never);
    wrap(
      <dl>
        <TargetRepository slug="platform" item={item} onDone={vi.fn()} />
      </dl>,
    );
    expect(await screen.findByTestId("ticket-repository")).toHaveTextContent("diamctl");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});
