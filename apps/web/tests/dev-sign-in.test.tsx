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
  fireEvent.click(screen.getByRole("button", { name: "enter" }));
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
    expect(screen.getByLabelText("development e-mail")).toHaveProperty("value", "admin@choregos.local");
    const url = submitted();
    expect(url.pathname).toBe("/api/v1/auth/login");
    expect(url.searchParams.get("as")).toBe("admin@choregos.local");
    expect(url.searchParams.get("redirect_to")).toBe("/p/demo");
  });

  it("still goes back where the person was going, when a page sent them here", async () => {
    query = new URLSearchParams({ next: "/inbox" });
    await open({ NEXT_PUBLIC_DEV_LOGIN: "true", NEXT_PUBLIC_DEV_LOGIN_NEXT: "/p/demo" });
    fireEvent.change(screen.getByLabelText("development e-mail"), { target: { value: "a@b.c" } });
    expect(submitted().searchParams.get("redirect_to")).toBe("/inbox");
  });

  it("offers nothing of it without development sign-in, whatever else was baked in", async () => {
    await open({ NEXT_PUBLIC_DEV_LOGIN_EMAIL: "admin@choregos.local", NEXT_PUBLIC_DEV_LOGIN_NEXT: "/p/demo" });
    expect(screen.queryByLabelText("development e-mail")).toBeNull();
  });
});
