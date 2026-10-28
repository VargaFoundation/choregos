import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The local install (S28-03) bakes development sign-in, the demo admin and the demo project into
// the console: the first sign-in is one click, and it lands on the demo project.
let query = new URLSearchParams();
vi.mock("next/navigation", () => ({ useSearchParams: () => query }));

async function open(env: Record<string, string>) {
  vi.resetModules();
  for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
  const { default: LoginPage } = await import("@/app/login/page");
  document.body.innerHTML = "";
  render(<LoginPage />);
}

function submitted(): URL {
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign });
  fireEvent.click(screen.getByRole("button", { name: "Enter" }));
  expect(assign).toHaveBeenCalledOnce();
  return new URL(String(assign.mock.calls[0]?.[0]), "http://localhost:3000");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  query = new URLSearchParams();
});

describe("development sign-in on the local install", () => {
  it("is one click: the demo admin is filled in and the sign-in lands on the demo project", async () => {
    await open({
      NEXT_PUBLIC_DEV_LOGIN: "true",
      NEXT_PUBLIC_DEV_LOGIN_EMAIL: "admin@choregos.local",
      NEXT_PUBLIC_DEV_LOGIN_NEXT: "/p/demo",
    });
    expect(screen.getByLabelText("Development e-mail")).toHaveProperty("value", "admin@choregos.local");
    const url = submitted();
    expect(url.pathname).toBe("/api/v1/auth/login");
    expect(url.searchParams.get("as")).toBe("admin@choregos.local");
    expect(url.searchParams.get("redirect_to")).toBe("/p/demo");
  });

  it("still goes back where the person was going, when a page sent them here", async () => {
    query = new URLSearchParams({ next: "/inbox" });
    await open({ NEXT_PUBLIC_DEV_LOGIN: "true", NEXT_PUBLIC_DEV_LOGIN_NEXT: "/p/demo" });
    fireEvent.change(screen.getByLabelText("Development e-mail"), { target: { value: "a@b.c" } });
    expect(submitted().searchParams.get("redirect_to")).toBe("/inbox");
  });

  it("offers nothing of it without development sign-in, whatever else was baked in", async () => {
    await open({ NEXT_PUBLIC_DEV_LOGIN_EMAIL: "admin@choregos.local", NEXT_PUBLIC_DEV_LOGIN_NEXT: "/p/demo" });
    expect(screen.queryByLabelText("Development e-mail")).toBeNull();
  });
});

describe("sign-in with the organisation's own identity provider (ADR 0107)", () => {
  function continued(): URL {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(assign).toHaveBeenCalledOnce();
    return new URL(String(assign.mock.calls[0]?.[0]), "http://localhost:3000");
  }

  it("sends the typed address as the login hint, so its domain picks the provider", async () => {
    query = new URLSearchParams({ next: "/inbox" });
    await open({});
    fireEvent.change(screen.getByLabelText("Work e-mail"), { target: { value: " bob@acme.example " } });
    const url = continued();
    expect(url.pathname).toBe("/api/v1/auth/login");
    expect(url.searchParams.get("login_hint")).toBe("bob@acme.example");
    expect(url.searchParams.get("redirect_to")).toBe("/inbox");
  });

  it("keeps the organisation of a sign-in link on both ways in", async () => {
    query = new URLSearchParams({ org: "acme" });
    await open({});
    const link = screen.getByRole("link", { name: "Continue with the identity provider" });
    expect(new URL(link.getAttribute("href") ?? "", "http://localhost:3000").searchParams.get("org")).toBe("acme");
    expect(continued().searchParams.get("org")).toBe("acme");
  });
});
