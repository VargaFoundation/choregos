import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GitHubAppRegistration, postManifest } from "@/components/admin/github-app-registration";
import { ApiError, api } from "@/lib/api";
import type { GitHubAppRegistrationStatus } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: { ...real.api, platformGitHubApp: vi.fn(), startGitHubAppManifest: vi.fn(), forgetPlatformGitHubApp: vi.fn() },
  };
});

const NONE: GitHubAppRegistrationStatus = {
  source: "none",
  usable: false,
  blocked_by: null,
  suggested_name: "Choregos acme.example",
  permissions: { contents: "write", metadata: "read" },
  events: ["issues", "pull_request"],
  webhook_url: "https://acme.example/api/v1/webhooks/github",
  callback_url: "https://acme.example/api/v1/github/setup",
  redirect_url: "https://acme.example/api/v1/github/manifest/callback",
};

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{node}</QueryClientProvider>);
}

describe("the instance's GitHub App, created from a manifest (S70-21)", () => {
  let submit: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    submit = vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(() => undefined);
  });
  afterEach(() => {
    submit.mockRestore();
    document.body.innerHTML = "";
    vi.mocked(api.startGitHubAppManifest).mockReset();
  });

  it("makes public or private an explicit choice before anything goes to GitHub", async () => {
    vi.mocked(api.platformGitHubApp).mockResolvedValue(NONE);
    wrap(<GitHubAppRegistration />);
    const button = await screen.findByRole("button", { name: "Continue on GitHub" });
    expect(screen.getByLabelText(/Name on GitHub/)).toHaveValue("Choregos acme.example");
    expect(screen.getByRole("radio", { name: /Private/ })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: /Public/ })).not.toBeChecked();
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /Private/ }));
    expect(button).toBeEnabled();
  });

  it("posts the manifest the API built to GitHub's page, as a form field", async () => {
    vi.mocked(api.platformGitHubApp).mockResolvedValue(NONE);
    vi.mocked(api.startGitHubAppManifest).mockResolvedValue({
      action_url: "https://github.com/organizations/acme/settings/apps/new?state=s1",
      manifest: { name: "Choregos acme.example", public: true },
    });
    wrap(<GitHubAppRegistration />);
    fireEvent.change(await screen.findByLabelText(/Owner organisation/), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("radio", { name: /Public/ }));
    fireEvent.click(screen.getByRole("button", { name: "Continue on GitHub" }));
    await waitFor(() => expect(submit).toHaveBeenCalled());
    expect(api.startGitHubAppManifest).toHaveBeenCalledWith({
      name: "Choregos acme.example",
      organization: "acme",
      public: true,
      webhook_active: true,
    });
    const form = document.querySelector("form[action^='https://github.com']") as HTMLFormElement;
    expect(form.method).toBe("post");
    expect(form.action).toBe("https://github.com/organizations/acme/settings/apps/new?state=s1");
    const field = form.querySelector("input[name='manifest']") as HTMLInputElement;
    expect(JSON.parse(field.value)).toEqual({ name: "Choregos acme.example", public: true });
  });

  it("says why the console cannot create one, and shows the deployment's App", async () => {
    vi.mocked(api.platformGitHubApp).mockResolvedValue({
      ...NONE,
      source: "deployment",
      app_id: "777",
      usable: true,
      blocked_by: "the deployment's settings define the GitHub App (CHOREGOS_GITHUB_APP_ID)",
    });
    wrap(<GitHubAppRegistration />);
    expect(await screen.findByText(/the deployment's settings define the GitHub App/)).toBeInTheDocument();
    expect(screen.getByText("777")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue on GitHub" })).toBeNull();
  });

  it("shows a stored App, the outcome of the flow, and forgets it on confirmation", async () => {
    vi.mocked(api.platformGitHubApp).mockResolvedValue({
      ...NONE,
      source: "manifest",
      app_id: "4242",
      name: "Choregos acme",
      owner_login: "acme",
      public: false,
      usable: true,
      created_by: "admin@acme.example",
      html_url: "https://github.com/apps/choregos-acme",
      blocked_by: "a GitHub App is already registered for this instance: forget it first",
    });
    vi.mocked(api.forgetPlatformGitHubApp).mockResolvedValue(undefined);
    wrap(<GitHubAppRegistration outcome="created" />);
    expect(await screen.findByText("The GitHub App is created")).toBeInTheDocument();
    expect(await screen.findByText("4242")).toBeInTheDocument();
    expect(screen.getByText("private")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Forget this App…" }));
    expect(api.forgetPlatformGitHubApp).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Forget it" }));
    await waitFor(() => expect(api.forgetPlatformGitHubApp).toHaveBeenCalled());
  });

  it("tells a reader who is not a platform administrator", async () => {
    vi.mocked(api.platformGitHubApp).mockRejectedValue(new ApiError(403, { title: "Forbidden" }));
    wrap(<GitHubAppRegistration />);
    expect(await screen.findByText(/Only a platform administrator/)).toBeInTheDocument();
  });

  it("builds a hidden form whose one field is the manifest", () => {
    const form = postManifest("https://github.com/settings/apps/new?state=x", { a: 1 });
    expect(form.style.display).toBe("none");
    expect(form.querySelectorAll("input")).toHaveLength(1);
    expect(submit).toHaveBeenCalledTimes(1);
  });
});
