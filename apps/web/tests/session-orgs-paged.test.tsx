// SPDX-License-Identifier: Apache-2.0
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: { ...real.api, me: vi.fn(), orgs: vi.fn() } };
});

import { api } from "@/lib/api";
import type { Org } from "@/lib/types";
import { SessionProvider, useSession } from "@/lib/session";

const org = (slug: string): Org => ({ slug, name: slug, role: "developer", currency: "EUR", aliases: [] }) as Org;

function Probe() {
  const { org: current, orgs, moreOrgs } = useSession();
  return <p>{`${current} | ${orgs.map((o) => o.slug).join(",")} | more=${moreOrgs}`}</p>;
}

describe("the session's organisations are paged by the server (ADR 0206)", () => {
  afterEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("finds the remembered organisation by the server's slug filter when the first page leaves it out", async () => {
    window.localStorage.setItem("choregos.org", "zulu");
    vi.mocked(api.me).mockResolvedValue({ email: "a@b.c", memberships: [] } as never);
    vi.mocked(api.orgs).mockImplementation(async (params = {}) =>
      params.slug === "zulu"
        ? { items: [org("zulu")], meta: { has_more: false, next_cursor: null } }
        : { items: [org("alpha"), org("beta")], meta: { has_more: true, next_cursor: "c" } },
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <SessionProvider>
          <Probe />
        </SessionProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText("zulu | alpha,beta,zulu | more=true")).toBeInTheDocument());
    expect(api.orgs).toHaveBeenCalledWith({ slug: "zulu" });
  });
});
